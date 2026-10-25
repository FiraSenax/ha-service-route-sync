# Changelog

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
