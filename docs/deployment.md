# Deployment guide

How to build, ship and operate GradingHelper on a department server. See `deploy/Dockerfile` and
`deploy/docker-compose.yml` for the artifacts this guide operates; `SPECIFICATION.md` §13 for the
deployment requirements they satisfy. Everything below assumes **Linux + Docker + docker
compose**, reachable only from inside the department network. Two topologies are covered:
sitting behind an **existing reverse proxy that terminates TLS** (the default in
`docker-compose.yml`, §3 below), and running **plain HTTP directly on the department LAN with no
proxy at all** (§3a) — this app itself never speaks TLS either way.

## 1. Prerequisites

- A Linux host with Docker Engine and the `docker compose` plugin (`docker compose version`).
- Confirmed with department IT: which host runs the reverse proxy, and whether it reaches this
  container over `localhost` (same host) or over the department network (different host). This
  decides the `ports:` line in step 3.
- Nothing else — the image is fully self-contained (Python, the `typst` toolchain, fonts, the
  vendored `cetz`/`cetz-plot` packages are all baked in at build time; see the Dockerfile's
  comments and `SPECIFICATION.md` §13).

## 2. Build

```
git clone <this repo> gradinghelper && cd gradinghelper
docker compose -f deploy/docker-compose.yml build
```

This is the **only** step that touches the network (fetching base images, npm/uv packages, and
the pinned `cetz`/`cetz-plot` archives — see `backend/scripts/vendor_typst_packages.py`). The
running container makes no outbound calls at all; a report render or PDF import never leaves the
machine, by design (§13's actual concern is that exam data — names, Matrikelnummern — is never
sent anywhere).

## 3. Configure

Open `deploy/docker-compose.yml` and check two things before first boot:

**Port binding.** The default, `127.0.0.1:8000:8000`, only works if the reverse proxy runs on
this same host. If it runs elsewhere, change it to `"8000:8000"` and restrict access at the
firewall instead — a container bound to all interfaces with no proxy in front is a direct,
unauthenticated-at-the-network-layer exposure of exam data.

**`GRADINGHELPER_COOKIE_SECURE`.** Defaults to `"1"`, correct for the assumed topology (reverse
proxy terminates TLS, so the browser always sees HTTPS). Two ways to get this wrong, both silent:

- `COOKIE_SECURE=1` but the proxy (or a direct HTTP connection during testing) doesn't actually
  give the browser HTTPS: the browser refuses to store the `Secure` cookie, so login *appears* to
  succeed (the API call returns 200) but every next request is unauthenticated. Set `"0"` only if
  you are deliberately running without TLS anywhere in front, e.g. an isolated trial network.
- The proxy terminates TLS but doesn't forward that fact to the app (no `X-Forwarded-Proto`
  header, or uvicorn not told to trust it): uvicorn's own redirect/URL-building logic thinks the
  request arrived over plain HTTP, which can manifest as a redirect loop. This is what
  `--proxy-headers --forwarded-allow-ips=*` in the Dockerfile's `CMD` is for — the app trusts
  `X-Forwarded-*` from whatever reaches it directly. That's safe under the assumed topology (only
  the proxy can reach the container port at all — see the port-binding note above), not safe if
  the container is ever reachable directly from other hosts.

Make sure the proxy config actually sends `X-Forwarded-Proto: https` (and `X-Forwarded-For`); the
exact directive depends on the department's proxy (`proxy_set_header X-Forwarded-Proto $scheme;`
for nginx).

The Dockerfile's `CMD` passes uvicorn `--forwarded-allow-ips=*` — trust `X-Forwarded-*` from
whoever reaches the container directly, which under the assumed topology (port bound to
`127.0.0.1`, only the proxy can reach it) is just the proxy. To restrict this to the proxy's
actual address instead, override the command in `docker-compose.yml`:

```yaml
services:
  app:
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
              "--proxy-headers", "--forwarded-allow-ips=<proxy IP>"]
```

**Reverse-proxy upload size.** The §5 registration-PDF import is a multipart file upload. Most
reverse proxies cap request body size well below a typical multi-page PDF — nginx defaults to
`client_max_body_size 1m`. Raise it in the proxy config (e.g. `client_max_body_size 20m;` in the
relevant nginx `server`/`location` block) before relying on this in production; otherwise the
import fails with a proxy-level 413 that has nothing to do with this app's own validation.

## 3a. Alternative: plain HTTP, no reverse proxy, LAN-only

If the department network itself is the trust boundary — server and clients on the same private
network, no proxy in front — the defaults above don't apply as-is; they assume a proxy is
terminating TLS. Override three things in `docker-compose.yml`:

```yaml
services:
  app:
    ports:
      - "8000:8000"     # not 127.0.0.1:8000:8000 — clients aren't on this host
    environment:
      GRADINGHELPER_COOKIE_SECURE: "0"   # see step 3's note: "1" here silently breaks login
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    # (drops --proxy-headers --forwarded-allow-ips=* from the image's default CMD — with no
    # proxy in front, nothing should be trusting X-Forwarded-* from arbitrary LAN clients)
```

**What this trades away**: without TLS anywhere, login credentials and the session cookie travel
in cleartext on the network — anyone who can observe that network segment (a shared VLAN, a
compromised machine on the same subnet, a rogue AP if any leg of the path is Wi-Fi) can read them.
This is the same trust model §13 assumes for the whole app (network-level protection standing in
for TLS), just made explicit rather than mediated by a proxy. It is not fixed by hashing the
password in the browser before sending it: an on-path attacker can either replay the hash
directly (the hash *is* the credential the server checks, so capturing it is exactly as good as
capturing the plaintext password, absent a real per-login challenge-response protocol) or simply
modify the served, unauthenticated-over-HTTP JavaScript to skip the hashing and exfiltrate the raw
password before it ever gets hashed. Application-layer tricks don't substitute for transport
security here — the session cookie in particular is the actual bearer credential for every
request after login, and no client-side hashing scheme touches that at all. If this trade-off
isn't acceptable, `docs/tls-setup.md` writes up two ways to get real TLS without an existing
department reverse proxy — a self-signed internal CA (manual one-time client trust) or a
publicly-trusted Let's Encrypt certificate via the DNS-01 challenge (zero client setup, needs a
DNS name). Neither is wired into this repo yet; that document is a reference to build from.

## 4. First boot

```
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml ps     # should show "healthy" after ~10s
```

The container runs its database migrations on startup (`alembic upgrade head`,
`backend/app/migrations.py`) — schema only at this point, no accounts exist yet. A version
upgrade that adds a new migration applies it the same way, on the next container start; nothing
extra to run by hand. Create the first admin account, interactively (the password is never passed
as a CLI argument or environment variable — see `backend/scripts/create_admin.py`'s docstring for
why):

```
docker compose -f deploy/docker-compose.yml exec app python scripts/create_admin.py --username <name>
```

This is a deliberately manual, one-time-per-deployment step, not something the compose file
automates — an auto-create-on-startup path needs a default password baked into the compose file,
which then quietly survives untouched into production. Log in at the proxy's URL for this app,
then use the admin UI (§3) to create instructor accounts — admins do not see exam data
themselves, account management only.

## 5. Backups

**Not automated by this app or this compose file** — §13 leaves the backup strategy to
department ops, deliberately, since it's an infrastructure decision (retention policy, existing
backup tooling) rather than an application concern.

The database runs in **WAL mode** (`app/db.py`), which means the live data is split across
`gradinghelper.db`, `gradinghelper.db-wal` and `gradinghelper.db-shm`. **A plain file copy of
just the `.db` file is not a valid backup** — it can miss committed transactions still sitting in
the WAL file, producing a snapshot that looks intact but silently drops recent data. Take a
WAL-safe backup instead, using SQLite's own online backup API from inside the running container
(no extra tooling needed — Python's `sqlite3` module ships with the image's Python):

```
BACKUP_NAME="backup-$(date +%F).db"
docker compose -f deploy/docker-compose.yml exec app python3 -c "
import sqlite3
src = sqlite3.connect('/app/data/gradinghelper.db')
dst = sqlite3.connect('/app/data/${BACKUP_NAME}')
src.backup(dst)
dst.close(); src.close()
"
docker compose -f deploy/docker-compose.yml cp "app:/app/data/${BACKUP_NAME}" ./backups/
docker compose -f deploy/docker-compose.yml exec app rm "/app/data/${BACKUP_NAME}"
```

The final `rm` matters: without it, every backup run leaves another full copy of student personal
data sitting inside the named volume itself, which is both wasted space and a second copy of
regulated data outside whatever retention plan the `./backups/` directory is under. Wrap the
whole block in a cron job on the host, pointed at a `./backups/` directory outside the named
volume (so a bad backup run can't corrupt the live data). `sqlite3 .backup` from the host works
identically if the department prefers a system-installed `sqlite3` CLI over this container-side
Python snippet — the point is "use SQLite's backup API," not the specific tool.

## 6. Bind mount vs. named volume

`deploy/docker-compose.yml` uses a Docker-managed **named volume** for `/app/data`. Docker
initializes a *fresh* named volume by copying the image's `/app/data` directory — including its
ownership (`gradinghelper:gradinghelper`, uid 10001) — so this works with no extra steps.

If department policy prefers a host **bind mount** instead (e.g. `./data:/app/data`, to make the
backup step above simpler or to reuse existing host backup tooling), that directory does **not**
get Docker's copy-up treatment — a bind mount is not Docker-managed storage at all, so it keeps
whatever ownership it already had on the host (whoever created the directory — often the
deploying user, sometimes `root` if Docker itself auto-created it on first `up`). Verified
directly: mounting a host directory owned by a non-10001 user with no chown fails —
`touch: cannot touch '/app/data/testfile': Permission denied` — every single time, not just under
some configurations. The container runs as uid 10001 and cannot write to a directory it doesn't
own or have group/other write access to. Before first boot with a bind mount:

```
mkdir -p ./data && sudo chown 10001:10001 ./data
```

## 7. Data retention / exam deletion

No automatic deletion, by design (§13) — nothing in this compose setup times anything out on its
own. The spec requires an explicit "delete exam" action (cascading to all of that exam's student
registrations and points — real personal data, Matrikelnummern included) so instructors can
comply with retention obligations once they expire; **as of this writing that action is not yet
implemented in the app** (tracked in `CLAUDE.md`'s status line under §15.6). Until it lands,
retention has no in-app path at all — don't plan around a feature that doesn't exist yet; deleting
exam data today means an operator manually removing rows from the SQLite file.

## 8. Updating

```
git pull
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
```

The named volume (and the data in it) survives a rebuild — only the image is replaced. Take a
backup (step 5) before updating across anything that touches the data model, same as any
production database.

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Login "succeeds" (200) but every next page redirects to login | `GRADINGHELPER_COOKIE_SECURE=1` with no real HTTPS reaching the browser — see step 3. |
| Redirect loop on login | Proxy not sending `X-Forwarded-Proto`, or the app not trusting it — see step 3. |
| Registration-PDF import fails before this app's own validation runs | Reverse-proxy body-size limit — see step 3. |
| Container starts but `docker compose ps` never shows `healthy` | Check `docker compose logs app`; the healthcheck hits `GET /api/health` directly inside the container, so a failure here means the app itself isn't serving, not a network/proxy issue. |
| A deep link (e.g. `/lectures/5`) 404s on a hard refresh, but works when clicked from within the app | Frontend build didn't make it into the image, or `app/main.py`'s SPA-fallback route regressed — rebuild and check `docker compose exec app ls /app/static`. |
