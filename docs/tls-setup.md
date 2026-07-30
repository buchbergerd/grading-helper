# TLS setup options (reference — not wired into this repo yet)

Two ways to get real TLS for GradingHelper without an existing department reverse proxy in
front of it (see `docs/deployment.md` §3a for the plain-HTTP alternative and why you might not
want it). Both are written up here as evaluated options; **neither is implemented** in
`deploy/Dockerfile` / `deploy/docker-compose.yml` — this is a reference to build from once one is
chosen, not a description of current behavior.

Every command below was actually run against this repo's code while writing this guide (a
self-signed CA + `uvicorn --ssl-keyfile/--ssl-certfile` chain was generated, verified, and
served — a `curl` client trusting only that CA got a valid `200 {"status":"ok"}` from
`/api/health`), so the syntax is confirmed, not copied from memory.

## Option A — Your own CA, installed once per client

**Idea**: create a small private Certificate Authority, install *its* public certificate on
every client device once, then issue (and freely re-issue) server certificates signed by that CA
without ever touching a client again. This is better than a single bare self-signed leaf
certificate, which would need every client re-trusted on every renewal — a CA needs trusting
only once.

**When it fits**: no department DNS name/API access available, but you can reach every client
device at least once (in person, via department IT's existing device management, or by emailing
one file and a two-minute instruction).

### 1. Create the root CA (once — this key is the whole trust anchor, protect it like a password)

```
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -subj "/CN=GradingHelper Internal CA" -out ca.crt
```

`ca.key` never leaves the machine that issues certificates. `ca.crt` is what gets installed on
every client — it contains no secret, just the CA's public key.

### 2. Issue a server certificate, signed by that CA

```
openssl genrsa -out server.key 2048
openssl req -new -key server.key -subj "/CN=gradinghelper.internal" -out server.csr

cat > server.ext <<EOF
subjectAltName = DNS:gradinghelper.internal, IP:<server's LAN IP>
EOF

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 825 -sha256 -extfile server.ext
```

The `subjectAltName` line is not optional in a modern browser — a certificate with only a `CN`
and no SAN is rejected outright by Chrome/Firefox/Safari today. List every hostname *and* IP
address anyone will actually type into the address bar; a cert issued for the wrong name fails
even though it's validly signed.

### 3. Point the app at the certificate

uvicorn can terminate TLS itself, no separate proxy needed:

```
uvicorn app.main:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile=/path/to/server.key --ssl-certfile=/path/to/server.crt
```

In compose terms: bind-mount `server.key`/`server.crt` into the container (never bake a private
key into the image or commit it to git — the repo's `.gitignore`/`.dockerignore` don't currently
allowlist anything under a plausible `certs/` path, and that's deliberate), and pass the two
paths via a `command:` override, matching what `docs/deployment.md` §3a already does for the
plain-HTTP `command:` override. Also re-enable `GRADINGHELPER_COOKIE_SECURE=1` — the whole point
of doing this is that the browser sees real HTTPS again.

### 4. Install the CA's certificate on every client — once, ever

- **Windows**: double-click `ca.crt` → *Install Certificate* → *Local Machine* → *Trusted Root
  Certification Authorities*. For many machines at once, department IT can push this via Group
  Policy instead of per-device clicking.
- **macOS**: open `ca.crt` in *Keychain Access* → drop it in the *System* keychain → double-click
  it → *Trust* → set to *Always Trust*.
- **Linux** (Debian/Ubuntu-family): `sudo cp ca.crt /usr/local/share/ca-certificates/gradinghelper-ca.crt && sudo update-ca-certificates`
  covers system tools and Chrome/Chromium. Firefox keeps its own certificate store and needs a
  separate import (`about:preferences#privacy` → *Certificates* → *View Certificates* →
  *Authorities* → *Import*, or `certutil -A -d sql:$HOME/.mozilla/firefox/<profile> -n "GradingHelper CA" -t "C,," -i ca.crt`
  for scripting it across machines).
- **iOS/Android**, if anyone needs mobile access: install `ca.crt` as a configuration profile /
  certificate via device settings. iOS additionally requires enabling full trust for it under
  *Settings → General → About → Certificate Trust Settings* — installing the profile alone isn't
  enough there.

### Renewing

Repeat step 2 only — a new leaf certificate signed by the same, already-trusted CA needs no
client action. Keep the CA's own validity long (the `3650` days above) and the leaf shorter (the
`825` days above is close to the current browser-enforced maximum for a leaf certificate);
recreate the leaf before it expires.

### Trade-offs

- Manual rollout to every device, once — real effort if the user base is spread out, low effort
  if it's a handful of instructors' machines or IT already manages them centrally.
- No revocation infrastructure (no CRL/OCSP responder) — if `server.key` ever leaks, the fix is
  "issue a new one," not "clients automatically stop trusting the old one." Fine at the scale
  this app runs at (§12: dozens–low hundreds of students, single-digit instructor users); would
  not scale as a public-internet CA.
- `ca.key` is a standing secret from the moment it's created — anyone who has it can mint a
  certificate any of your clients will trust for anything, not just this app.

## Option B — A publicly-trusted certificate via Let's Encrypt, DNS-01 challenge

**Idea**: get a real certificate from a CA every browser already trusts, so there is zero
client-side setup, ever. The usual way (HTTP-01 challenge) needs the server reachable from the
public internet on port 80, which contradicts the whole point of an internal-only deployment.
The **DNS-01** challenge instead proves domain control by creating a TXT record — it never
requires the server itself to be reachable from outside the department network, only that the
DNS zone is real and you (or department IT) can create a record in it. A public DNS record
pointing at a private IP is completely normal and doesn't expose the service — only clients that
can already route to that private IP can ever reach it.

**When it fits**: you can get a DNS name (even a subdomain of an existing department domain) and
API credentials to whoever hosts its DNS. Preferable to Option A whenever this is available —
same or less ongoing effort, and it's a standard trusted certificate rather than a private CA
nobody outside this app's users has ever heard of.

### Prerequisites

- A DNS name, e.g. `gradinghelper.dept.example.edu`, with an A/AAAA record pointing at the
  server's IP (the department's internal IP is fine).
- An API token from whichever provider hosts that DNS zone, scoped as narrowly as the provider
  allows (ideally: only that one record/zone, not full account access). This token is a secret —
  it goes in an untracked `.env` file or a Docker secret, never in `docker-compose.yml` itself or
  in git.

### Recommended tool: Caddy

Caddy has automatic HTTPS built in and handles renewal with no cron job or operator action.
DNS-01 needs a DNS-provider-specific plugin, which means building (or pulling a prebuilt) Caddy
image with that plugin compiled in via [`xcaddy`](https://github.com/caddyserver/xcaddy) — plain
`caddy:2-alpine` only supports the HTTP-01/TLS-ALPN-01 challenges, not DNS-01, out of the box.

A `Caddyfile` for this app (assuming a Cloudflare-hosted zone — substitute whichever DNS module
matches the department's actual provider):

```
gradinghelper.dept.example.edu {
    reverse_proxy app:8000
    tls {
        dns cloudflare {env.CF_API_TOKEN}
    }
}
```

Sketch of the compose addition (illustrative — not present in `deploy/docker-compose.yml` today):

```yaml
services:
  app:
    # no ports: published directly — only Caddy is reachable from the network
    environment:
      GRADINGHELPER_COOKIE_SECURE: "1"   # Caddy gives the browser real HTTPS
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
              "--proxy-headers", "--forwarded-allow-ips=*"]  # trust Caddy, the only thing that can reach this container

  caddy:
    build: ./caddy   # xcaddy build with the DNS provider plugin
    ports:
      - "443:443"
    environment:
      CF_API_TOKEN: ${CF_API_TOKEN}   # from an untracked .env file
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data   # certificates persist here across restarts

volumes:
  caddy-data:
```

`app`'s own `--proxy-headers --forwarded-allow-ips=*` is now safe under the same reasoning as
the department-proxy case in `docs/deployment.md` §3: Caddy is the only thing with network access
to the `app` container (no published port on it), so trusting forwarded headers from "whoever
reaches it directly" means trusting only Caddy.

### Renewal

Fully automatic — Caddy renews well before Let's Encrypt's 90-day certificate expiry and reloads
itself with no downtime and no operator action. A brief internet or DNS-provider outage during a
renewal attempt isn't a problem; there's a wide margin before the old certificate actually
expires.

### Trade-offs

- One more container than Option A, and a build step (`xcaddy`) to get the right DNS plugin in.
- A genuine external dependency: DNS provider API access must keep working indefinitely for
  renewal to keep working. Losing that access (token revoked, provider changed) eventually breaks
  TLS, silently, until someone notices — worth a calendar reminder to check on it occasionally, or
  monitoring on certificate expiry.
- Needs a DNS name and API credentials to be obtainable in the first place — a people/process
  dependency (department IT, or whoever owns the domain), not a technical one.

## Comparison

| | Option A: own CA | Option B: Let's Encrypt DNS-01 |
|---|---|---|
| Client-side setup | Once per device, manual (or IT-pushed) | None, ever |
| Needs a DNS name | No | Yes |
| Needs a DNS provider API token | No | Yes |
| Extra moving parts | None (uvicorn terminates TLS directly) | One more container (Caddy) + a custom build |
| Ongoing risk | `ca.key` compromise, no revocation | DNS/API access lapsing silently |
| Certificate is "real" (any client, no warning) | No — only clients that installed the CA | Yes — every browser everywhere |

**Recommendation**: if a DNS name and DNS-provider API access are realistically obtainable, go
with Option B — it's less ongoing operational burden (no per-device rollout, ever) for
comparable setup effort. Fall back to Option A when getting a DNS name/API token is a dead end
but you can reach every client device at least once, or when department IT already runs (and
already deployed to campus machines) an internal CA you can request a certificate from instead of
minting a new one — check that first, since it would mean skipping the client-install step
entirely too.
