# Validation and compatibility

This record describes release **0.5.1**, commit
[`c8428c4`](https://github.com/FiraSenax/ha-service-route-sync/commit/c8428c47329fd7ce44482fa6777f0a56d721264e),
checked on **2026-10-25**. It separates tested behavior from supported API
contracts and environments that have not been independently exercised.

## Automated checks

All six jobs in the [release CI run](https://github.com/FiraSenax/ha-service-route-sync/actions/runs/40227490909)
passed. [Extended CodeQL analysis](https://github.com/FiraSenax/ha-service-route-sync/actions/runs/40227490827)
completed for the same commit with zero results.

| Check | Environment and scope |
| --- | --- |
| Unit tests | 211 tests; Python 3.14 in CI; route planning, observations, bounded parsing, history and protocol behavior |
| HA runtime tests | 182 tests; Python 3.14 with Home Assistant 2026.10.0; flows, entities, authorization, setup/unload and recovery behavior |
| Gateway tests | 52 tests; Python 3.14; restricted commands, input validation, rule ownership and injected write failures |
| Frontend tests | 6 tests; Node.js 22; Wi-Fi card behavior |
| Browser test | 1 Chromium scenario; Python 3.13 and Playwright 1.58.0; rendering, pagination, source selection, permissions and text safety |
| Static and packaging checks | Ruff 0.17.0, Python compilation, generated Windows-reader consistency, HACS and Hassfest |
| Security analysis | CodeQL for Python and JavaScript/TypeScript using `security-extended` |

The Python unit, HA and gateway suites total **445 tests**; the browser scenario
is additional. Test counts are not a coverage percentage. Most controller and
gateway failure cases use fixtures or simulated backends, not physical routers.
HACS and Hassfest success does not itself approve inclusion in the default HACS
catalog; that has a separate [maintainer review](https://github.com/hacs/default/pull/11881).

## Live evidence

One existing installation was checked after updating to 0.5.1 on Home Assistant
OS with Home Assistant **2026.10.3**:

- The integration loaded without feature setup issues. Installed component
  files matched the release source, with both obsolete migration adapters absent.
- Configuration schema 2.1, registry identities, the accepted Windows cursor and
  monitoring history remained intact. No old configuration namespace was needed.
- Controller, client, DNS and Windows monitoring sources returned observations.
  Client, work and monitoring metrics all returned HTTP 200.
- The separately enabled IPv6 writer reported a current primary-mode policy and
  no review latch. During the preceding 0.5.0 gateway update, outbound IPv6 from
  a LAN ULA reached both fixed test targets with 3/3 replies each. The 0.5.1
  cleanup did not change gateway code.

These are checks of one installation at a particular time. They are not a
multi-vendor, multi-firmware or long-duration reliability study. The 0.5.1 cleanup
check did not exercise new route writes or a forced WAN outage.

## Compatibility boundaries

| Area | Supported contract and limits |
| --- | --- |
| Home Assistant | HACS metadata declares 2026.9.0 as the minimum. CI uses 2026.10.0; the live check used 2026.10.3. Other versions, including the declared minimum, are not covered by a CI version matrix. |
| UniFi controller | Own local Network API key and the documented UniFi OS compatibility paths. `/integration/v1`-only controllers and other route schemas are not implemented. No firmware compatibility matrix has been established. |
| Gateway observations | Restricted OpenSSH command plus the reader's documented Python/Linux/UbiOS tools and configuration. An available SSH server alone is insufficient. |
| IPv6 writes | Exact managed chains/hooks and policy preference, matching ULA/delegated prefix lengths from /48 through /64, explicit interfaces/tables/marks and exclusive writer ownership. Other firewall layouts need a separate backend. |
| Public HTTPS probes | Standalone operation without UniFi; requires `/usr/bin/curl` and uses the shipped fixed destinations. |
| Windows events | Optional Wazuh reader with its documented schema and configured agent. Other event collectors are not implemented. |
| Other routers | FRITZ!Box and other router backends are not implemented. |

See [configuration](CONFIGURATION.md), [architecture](ARCHITECTURE.md) and
[permissions](PERMISSIONS.md) before selecting features. Optional modules share
Home Assistant's process privileges; package boundaries are not sandboxes.

## Remaining quality work

- Measure line and branch coverage and establish meaningful CI thresholds.
  No coverage percentage is currently published or enforced.
- Add static type checking for module interfaces. Ruff checks and compilation
  currently run, but there is no mypy or Pyright CI job.
- Test clean installations and updates across additional HA/controller/gateway
  versions, plus longer operation and real network-failure scenarios.
- Obtain independent review of the privileged gateway paths and authorization
  boundaries. A clean CodeQL result is not proof that no vulnerability exists.

Removal of the private migration adapters means automatic upgrades from the old
experimental schemas are unsupported. Current-schema installations keep their
state; new users use the regular setup flow. Documentation should describe this
current behavior rather than private deployment history.

## Reproducing the checks

Run from the repository root with Python 3.14:

```sh
python3.14 -m venv .venv
.venv/bin/pip install homeassistant==2026.10.0 ruff==0.17.0
.venv/bin/python scripts/sync_windows_reader.py --check
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests_gateway -v
.venv/bin/python -m unittest discover -s tests_ha -t . -v
.venv/bin/python -m compileall -q custom_components
```

With Node.js 22:

```sh
node --input-type=module --check < custom_components/service_route_sync/frontend/wifi-history-card.js
node --test tests_frontend/*.test.mjs
```

The browser job uses its own Python 3.13 environment on Linux:

```sh
python3.13 -m venv .venv-browser
.venv-browser/bin/pip install playwright==1.58.0
.venv-browser/bin/python -m playwright install --with-deps chromium
.venv-browser/bin/python -m unittest discover -s tests_browser -v
```

HACS, Hassfest and CodeQL run in GitHub Actions. The
[test workflow](../.github/workflows/tests.yml) and
[security workflow](../.github/workflows/security.yml) are the source of truth
for their commands and pinned actions. Update this dated record when a release
changes the tested environments or evidence.
