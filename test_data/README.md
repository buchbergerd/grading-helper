# Test data

- `ZPrf_Grundlagen_der_Informationstechnik_B_Sc_WiIng_ET_IT_WiSe_23_24_TestData.pdf` — the one
  real (anonymized) sample registration export provided by the user. 2 rows, 1 page — build the
  parser against this first, but note it does **not** exercise multi-page parsing or the §5.3
  row-count checksum.
- A synthetic multi-page fixture (`*_synthetic*.pdf`) is still needed for those two cases — see
  `/scripts/README.md`.

**Only anonymized/synthetic PDFs belong in this directory.** The root `.gitignore` blocks any
`*.pdf` here except the two patterns above, so a real (non-anonymized) export dropped in for
local debugging is never accidentally committed.
