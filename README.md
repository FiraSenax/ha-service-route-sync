# Service Route Sync

[Architecture, security boundaries and compatibility](docs/ARCHITECTURE.md) · [Security policy](SECURITY.md)

[Deutsch](README.de.md) · English

[![Tests](https://github.com/FiraSenax/ha-service-route-sync/actions/workflows/tests.yml/badge.svg)](https://github.com/FiraSenax/ha-service-route-sync/actions/workflows/tests.yml)

Service Route Sync keeps the destination IP prefixes of selected games and cloud services in existing UniFi traffic routes up to date. It runs inside Home Assistant, uses its own explicitly configured local UniFi API key, and needs no external scheduler.

**Route changes start in preview mode.** Controller access and route write/restore paths have automated tests with simulated backends. This release still requires live validation in your setup before automatic writes.

**Upgrading to 0.3:** enter this integration’s own credentials and configure your control helpers, diagnostic profile and notification target. There is no personal configuration fallback. See [configuration and rollout](docs/CONFIGURATION.md).

## Services

| Selection | Source and scope |
| --- | --- |
| Call of Duty / Demonware | RIPE, AS60229 |
| Blizzard / Battle.net | RIPE, AS57976 |
| Riot Games | RIPE, AS6507 |
| PUBG / Krafton | RIPE, AS18321 |
| Destiny / Bungie | RIPE, AS394073 |
| i3D.net | RIPE, AS49544; shared hosting also serves other games |
| Microsoft 365 | Published IP endpoints across all M365 service areas, filtered by category |
| Teams, Exchange, SharePoint / OneDrive | Selected service area plus `Common`, filtered by category |
| Azure | Explicitly selected service tags, such as `Storage.WestEurope` |
| AWS | Explicitly selected regions and services, such as `eu-central-1` and `S3` |
| SAP | Documented **destination** prefixes for the SAP services you use; see [Cloud ALM guidance](docs/SAP_DESTINATIONS.md) |
| Custom services | Public IPv4/IPv6 destination networks in CIDR notation |

The game selections cover known operator ASNs, not every server a game might use. Games can use additional cloud, CDN, or third-party networks. Microsoft endpoints without a published IP address are not represented. SAP egress or NAT addresses are not automatically valid destinations for client traffic.

Microsoft 365 defaults to the **Optimize** category; **Allow** and **Default** can also be selected. For Azure and AWS, select only the tags, services, and regions you need. Routing an entire cloud can affect unrelated applications. Overlapping services, such as M365 and Teams, require deliberate route priority and device scope in UniFi.

## Router support

| Backend | Status |
| --- | --- |
| UniFi Network | Implemented; independent local UniFi credentials |
| FRITZ!Box | Not implemented; equivalent traffic-route control has not been established |
| Other routers | Backend interface exists, but concrete support is pending |

The integration only updates `ip_addresses` on explicitly bound IP routes. It neither creates nor deletes routes. WAN selection, source devices or VLANs, enabled state, kill switch, and existing IP-range anchors are preserved. A disabled route stays disabled.

For monitored clients, **Connection type** reports wired, Wi-Fi, or disconnected. Switch-port speed and counters are available only for a currently occupied wired switch port. They remain unavailable for Wi-Fi clients. A temporary UniFi API failure does not discard the last valid client snapshot.

## Installation

You need Home Assistant **2026.9 or newer** and a reachable **UniFi Network** controller. Use a dedicated local Network API key. Required operations and controller permission limits are documented in [Permissions](docs/PERMISSIONS.md).

1. In HACS, add `https://github.com/FiraSenax/ha-service-route-sync` as a custom **Integration** repository. A [request for inclusion in the default catalog](https://github.com/hacs/default/pull/11881) is pending.
2. Download **Service Route Sync** and restart Home Assistant.
3. In **Settings → Devices & services**, add **Service Route Sync**.
4. Enter the controller host, port, site and dedicated local credentials, then select services and cloud filters.
5. Bind each service to its own existing UniFi IP route. Create those routes in UniFi first, with the intended WAN and device or network scope.

## Operation

After setup, Home Assistant provides:

- **Status:** `preview`, `current`, or `review_required`.
- **Destination prefixes:** number of selected destination prefixes.
- **Routes with changes:** route count and per-service additions or removals in attributes.
- **Last applied change:** time of the last fully verified change.
- **Refresh destinations:** refresh sources and preview; when automation is enabled, verified changes may also be applied.
- **Automatic route updates:** off by default. Enabling it starts a fresh check.
- **Restore last route update:** restores one saved route update while automation is off, if nobody has changed the route since.

Disable any other script or integration that writes the same UniFi routes before enabling automatic updates. Configure one Service Route Sync entry per controller endpoint and site.

The default refresh interval is 24 hours and can be set from one hour to one week. Successfully fetched public feeds are cached for one hour. A refresh during that window reuses the feed but reads the router again. Changing integration options turns automatic route updates off.

## Safeguards and recovery

All selected feeds and routes are checked before the first write. Empty or malformed feeds, mismatched ASN owners, private or default-route destinations, port filters, and exceeded prefix limits stop the cycle. Prefix lists are never silently truncated.

The preview includes large changes. Automatic application is blocked by default when more than 50% of existing prefix entries would be removed. This threshold is configurable and can be conservative when equivalent networks are reorganized into different CIDRs.

Immediately before each update, the route is read again and a local Home Assistant Store backup is saved. The result is then read back, including policy fields that should not have changed. An ambiguous response, failed verification, or interrupted write leaves a persistent write lock. After review, automation can be turned off and on again.

Changes to multiple routes are **not atomic**. If a later route fails, earlier ones may already have changed and further automatic writes remain blocked. The last 20 individual route backups are kept locally. Restore works one route at a time in reverse order and refuses to overwrite later changes. Removing the integration does not remove router routes.

Diagnostics include service names and counters, not credentials, WAN IDs, source devices, or destination IP lists. Local backups contain router configuration and should be protected like other Home Assistant configuration data.

## Optional network diagnostics

WAN links, gateway traffic, selected clients, and Wi-Fi history can be observed directly in Home Assistant. In the integration options, enable **Monitor internet connections**, choose a gateway, and name its two links (for example, DSL and Starlink). Each link exposes download, upload, latency, availability over the measurement window, and the link state if UniFi explicitly provides an `up` value. A missing link state stays unavailable.

Local monitoring polls independently of public destination feeds. A failed feed does not block WAN readings. WAN measurements alone do not prove IPv4 or IPv6 reachability and do not trigger failover.

Active IPv6 policy control and additional host observers are separate opt-in features with their own restricted SSH keys and pinned host identities. Read-only observers cannot alter routes. Write-capable features maintain a local verification and lock state. See the [network architecture](docs/NETWORK_ARCHITECTURE.md), [gateway observer](docs/GATEWAY_OBSERVER.md), and [IPv6 actor](docs/IPV6_ACTOR.md) documents for setup and limits.

## Languages

The Home Assistant setup and entities include complete English (`en`) and German (`de`) translation files. The bundled Wi-Fi history card also follows the user's HA language for English and German. Other locales use English until a translation is added. Additional translations can be contributed as BCP 47-named files in [`translations/`](custom_components/service_route_sync/translations/); keep every key and placeholder from `en.json`, and add the card's text and column labels in `frontend/wifi-history-card.js`. HACS lists the repository once, independently of interface language.

## Development

```sh
python -m venv .venv
.venv/bin/pip install homeassistant==2026.10.0 ruff==0.17.0
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests_gateway -v
.venv/bin/python -m unittest discover -s tests_ha -t . -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

`core.py` contains router-independent planning and write checks, `providers.py` contains feed selection, and `backends/unifi.py` contains the UniFi adapter. The adapter owns its `aiohttp` session and explicitly configured local API key, so upstream API changes may require updates. GitHub Actions runs tests, formatting, Python compilation, Hassfest, and HACS validation.

## Sources

- [Home Assistant UniFi Network](https://www.home-assistant.io/integrations/unifi/)
- [UniFi Policy-Based Routing](https://help.ui.com/hc/en-us/articles/12566175125783-UniFi-Gateway-Policy-Based-Routing)
- [RIPEstat announced prefixes](https://stat.ripe.net/docs/data-api/api-endpoints/announced-prefixes)
- [Microsoft 365 Endpoint Web Service](https://learn.microsoft.com/en-us/microsoft-365/enterprise/microsoft-365-ip-web-service)
- [Azure Service Tags](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview)
- [AWS IP address ranges](https://docs.aws.amazon.com/vpc/latest/userguide/aws-ip-ranges.html)
- [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/)

MIT licensed. Not an official product of Ubiquiti, Microsoft, AWS, or SAP.
