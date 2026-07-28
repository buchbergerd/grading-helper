# Scripts

Repo-root cross-cutting scripts live here. Backend-specific scripts (including the synthetic
fixture generator described below) live in `/backend/scripts/`, next to the `pyproject.toml`/`uv`
environment they need — see `/backend/scripts/README.md`.

## Synthetic registration-PDF fixtures

SPECIFICATION.md §5.2 needed a synthetic, anonymized, **multi-page** registration PDF fixture to
test multi-page parsing and the §5.3 row-count checksum — the real sample fixture in
`/test_data/` only has 2 rows on 1 page and doesn't exercise either. This is now written:
`/backend/scripts/make_fixtures.py`, documented in `/backend/scripts/README.md`.
