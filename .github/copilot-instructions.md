# Service Route Sync

This repository is a HACS integration in `custom_components/service_route_sync`.
Read `docs/ARCHITECTURE.md`, `docs/PERMISSIONS.md` and `docs/RELEASING.md` before
changing trust boundaries, compatibility or publishing behavior.

## Home Assistant compatibility

- Use Home Assistant's public APIs and the Python series required by the tested
  HA release. CI resolves the exact minimum from `hacs.json` and latest stable HA
  on every run. Never silently raise the minimum or claim intermediate versions
  were tested. Keep HA's own dependency pins; use a separate environment for audit tools.
- Keep blocking I/O and expensive parsing outside the HA event loop. Clean up
  listeners, tasks and coordinators on unload. Optional feature failures must
  not disable independent features. Preserve schema 2.1 state and entity IDs.
- Use this integration's dedicated local UniFi API key. Do not reuse another
  integration's client, credentials or internal APIs. Keep EN/DE translations and
  the frontend's language keys/placeholders consistent.

## Security and behavior

- Preserve administrator/trusted-automation checks on writes, imports, history
  and privileged recovery; retain entity permissions on read endpoints.
- Route updates start in preview mode. Preserve backups, concurrent-change
  checks, exact readback, rollback boundaries and persistent uncertain-write latches.
  Never turn unknown, stale or contradictory observations into a confirmed outage.
- Keep TLS verification/certificate pins and SSH host-key pins. Keep credentials
  separated per gateway capability, fixed forced commands, bounded input/output,
  decompression limits and deadlines. Diagnostics/logs must omit secrets and raw
  controller, router and Windows event payloads.
- Tests use fixtures and simulated peers. Do not contact or modify real HA,
  UniFi, gateways or private monitoring systems without explicit owner instruction.
- Keep CI Actions SHA-pinned, default tokens read-only and checkout credentials
  unpersisted. Do not bypass CI, CodeQL/SARIF checks, vulnerability audits or tag
  protections; do not suppress findings or expand token permissions to make CI pass.
- Propose changes through PRs. Copilot suggestions do not replace human judgment
  or required checks. Do not approve/merge PRs or publish releases automatically.

## Validation and reviews

Install the local development pins with `python -m pip install -r requirements-test.txt`.
Run checks relevant to the change:

```sh
python scripts/sync_windows_reader.py --check
ruff check .
ruff format --check .
python -m unittest discover -s tests_ci -v
python -m unittest discover -s tests_workflows -v
python -m unittest discover -s tests -v
python -m unittest discover -s tests_gateway -v
python -m unittest discover -s tests_ha -t . -v
node --test tests_frontend/*.test.mjs
python -m unittest discover -s tests_browser -v
```

Browser tests need `requirements-browser.txt` and Playwright Chromium installed.
The canonical Windows reader is in the HA `windows/` package; regenerate copies
with `python scripts/sync_windows_reader.py`, never edit generated copies alone.
Review for concrete correctness, authorization, async lifecycle, migration and
packaging regressions. Explain the trigger and impact with file/line evidence;
prioritize actionable bugs over stylistic preferences. Report actual test results
and distinguish fixture coverage from live deployment evidence.
