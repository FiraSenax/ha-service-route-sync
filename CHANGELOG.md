# Changelog

## Unreleased

## 0.6.0 — HACS compatibility and verified releases

**Upgrade requirement:** Home Assistant **2026.11.0 or newer** is now required,
up from 2026.9.0. Update Home Assistant before installing this release.

- Keep the integration's runtime behavior and configuration schema 2.1 unchanged from 0.5.1. Existing settings, entity IDs and history remain intact; older experimental schemas remain unsupported.
- Document installation from the default HACS catalog, where Service Route Sync is now available, and clarify gateway setup and security boundaries.
- Test the exact declared minimum and dynamically resolved latest stable HA release on PRs, daily and before publishing. Retain each HA release's own Python and dependency requirements and audit its installed packages separately.
- Require matching manifest/changelog versions, full compatibility, HACS/Hassfest, browser/frontend, workflow-policy and CodeQL checks before release publication. Check publication prerequisites before running release tests and repeat them immediately before creating a fresh tag at the tested commit.
- Read back the published stable release, notes, exact tag commit and latest status before reporting success. Detect uncertain publication outcomes without retrying writes or replacing existing tags/releases; provide a read-only verification command and a workflow summary.
- Protect main and release tags, enable vulnerability reporting and dependency/malware alerts, and add Copilot review guidance. Test dependencies are updated to HA 2026.11.1 and Playwright 1.65.1; Playwright is only a development/CI dependency.

## 0.5.1 — remove private migration paths

- Remove the experimental entry-schema adapter and old checkpoint converter.
- Remove the obsolete adapter tests and automatic-upgrade documentation.
- Keep configuration schema 2.1 and existing accepted state unchanged. New users
  use the regular setup flow; upgrades from experimental schemas are unsupported.

## 0.5.0 — modular features and security hardening

- Require administrator or trusted automation context for route controls, including
  refreshes that can trigger writes. Sanitize unexpected route-action errors.
- Bound controller JSON to 4 MiB and 64 nesting levels; reject duplicate keys and
  non-finite values, and decode outside the event loop. Bound gzip/deflate output
  allocation for controller and feed responses.
- Separate routing, network, clients, monitoring, WLAN, IPv6, Frigate, probes and
  Windows protocols into feature packages and compose smaller configuration flows.
- Isolate optional feature setup failures and centrally manage shutdown.
- Automatically migrate schema-1 HA configuration, registry identities, checkpoints
  and history, preserving entity IDs and recovery sources. Old writers stay paused.
- Allow standalone public probes and independent client/work metric endpoints.
- Configure the DNS probe name and IPv4/IPv6 resolvers; explicitly select WAN
  interfaces and support matching ULA/provider prefixes from /48 through /64.
- Generate the standalone Windows reader from canonical sources and verify parity
  in CI. Pin Actions to commits; add Dependabot and extended CodeQL analysis.


## 0.4.0 — neutral client monitoring

- Use neutral client-monitoring names throughout modules, configuration, entities,
  services, metrics and gateway commands. Remove personal profile names from fixtures.
- Breaking: configuration schema 2 replaces the previous diagnostic namespace.
  Existing installations must migrate their configuration, registry identifiers,
  checkpoint/history paths and restricted gateway command together, or recreate
  the integration with the new configuration. No legacy runtime aliases are retained.
- Update monitoring consumers to `/metrics/monitoring` and `monitoring_*` metrics.


## 0.3.0 — explicit public configuration

- Breaking: configure this integration's own UniFi host, site and local Network API key;
  no official integration client or credentials are reused. Existing entries
  request reauthentication and automatic route updates are disabled afterward.
- API-key-only setup: no username/password form or login. Required API operations,
  least-privilege recommendations and controller permission limits are documented.
- Select IPv6 control helpers and WAN preference values. Gateway policy interfaces
  and tables are configurable; protocol roles are `primary` and `secondary`.
- Configure diagnostic profile IDs/names and notification services. Windows/Wazuh
  observations are optional; the gateway reader's agent and paths are configurable.
- New diagnostics installations no longer require a personal imported checkpoint;
  observation mode does not queue future notifications.
- Use Home Assistant's time zone for incident and metric calculations.
- Preserve arbitrary existing Fortnite IP-range anchors instead of requiring two
  fixed ranges. Route write and recovery safeguards are retained.
- Use the configured routing-mark mask for IPv4 source routing and CONNMARK;
  support current UniFi WAN marks instead of assuming an older fixed mask.
- Windows daily archive replay now continues beyond 64 MiB with an explicit
  configurable archive bound, preserving existing cursors and bounded responses.
- Client diagnostics explain when PC monitoring is suspended and avoid network
  incident alerts for normal shutdown or absent PC activity.

See [configuration and rollout](docs/CONFIGURATION.md). Gateway configurations and
personal helper defaults are not automatically migrated. Upgrade the gateway
readers and configure the new fields before enabling automated actions.


## 0.2.2

- Wi-Fi history card now follows the Home Assistant language for English and German, including errors, controls, and access messages.
- Unknown languages use English card text; changing HA language rerenders the card without refetching private history.

## 0.2.1

- English README for international HACS readers, with the German version kept separately.
- Document the existing English and German HA translations and how to contribute further languages.

## 0.2.0

- Optionale WAN-, Gateway-, Client- und WLAN-Beobachtung direkt in Home Assistant.
- Getrennte, ausdrücklich einzurichtende IPv6-Policy-Steuerung mit geprüfter Rücklesung und dauerhafter Sperre bei unklarem Zustand.
- Begrenzter WLAN-Verlauf mit eigener Karte sowie optionale Frigate- und Windows-Beobachter.
- Zwei private DNS-Resolver für die zusätzliche Netzwerkdiagnose in den HA-Optionen konfigurierbar.
- Automatisierte Prüfungen mit HA 2026.10, HACS, Hassfest, Chromium und Frontend-Tests.
- SAP-Cloud-ALM-Regionen und die Grenzen der manuell gepflegten Ziel-CIDRs dokumentiert.

## 0.1.0

- Erste HACS-Custom-Integration mit bestehender HA-UniFi-Anbindung.
- Auswählbare Spiele-ASNs, Microsoft 365, Azure Service Tags, AWS-Filter und eigene SAP-/Dienstpräfixe.
- Vorschau, explizite Schreibfreigabe, lokale Sicherungen, geschützte Wiederherstellung und persistente Sperre bei unklaren Schreibvorgängen.
- Deutsche und englische Einrichtung; Statussensoren und Bedienelemente.
