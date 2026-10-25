# Configuration

Configure this integration's own connection, helper entities, profile identities
and notification service. No credentials are copied from another integration and
no personal helper names are used as fallbacks.

## UniFi connection

In **Settings → Devices & services → Service Route Sync**, enter the controller
host (without a URL scheme), HTTPS port, site and a dedicated **local UniFi
Network API key** created in the controller's Integrations settings. This is the
only authentication method; no username or password is requested. The official
Home Assistant UniFi integration is not required. Do not use a Site Manager
cloud API key. See [permissions and least privilege](PERMISSIONS.md) before
creating the key.

Normal TLS verification is used unless you supply an independently verified
SHA-256 certificate fingerprint. A self-signed certificate requires that
fingerprint. Leaving the API key empty when editing options preserves the key
already saved by Service Route Sync.

Existing entries without these settings open a reauthentication flow. All
features wait for an explicitly configured connection. Reauthentication disables
automatic route updates; inspect the new preview before enabling them again.

Route writes additionally require an explicitly configured route API key and
certificate pin in **Route TLS**. Use a separate key for route changes where
possible. Separate keys allow independent revocation; they do not automatically
have different permissions. Client and device observations use the connection
key. Route reads use the route key when configured, otherwise the connection key.
Keys are sent only to the configured controller, never to public feed providers.

This transport requires UniFi OS's `/proxy/network/api/s/{site}/stat/...` and
`/proxy/network/v2/api/site/{site}/trafficroutes` endpoints. It does not implement
the official `/integration/v1` API. Controller versions and permissions must be
checked against these exact endpoints; other layouts are not supported.

## IPv6 policy

Select four distinct `input_boolean` helpers for enable, dry-run, NAT66 and
source routing, plus one `input_select` helper for the WAN preference. Configure
the exact three selection values for automatic, primary and secondary WAN.
Names such as `Automatic`, `Fiber` and `Mobile` are valid. These values map to
internal roles `auto`, `primary` and `secondary`. WAN display labels are entered
in the network settings. Missing, unavailable or invalid helpers stop planning;
there is no fallback to a personal entity ID.

Provision the matching gateway actor from this release before activating it.
Old configurations and journals using `dsl`/`starlink` modes are not translated.
Deactivate old writers and resolve any pending journal before replacing the
configuration. An unrecognized journal is rejected rather than discarded.
See [IPv6 actor provisioning](IPV6_ACTOR.md) for the configuration format and
write safeguards. Primary and secondary interfaces must match the WAN observer.

## Client diagnostics

Enable **Client network diagnostics** and choose a unique profile ID, display
name and MAC addresses. The profile must not overlap other monitored clients.
The restricted gateway client observer accepts any single configured profile;
its target ID must match the HA profile ID.

Choose a `notify` service exposed by Home Assistant, for example a mobile-app
notification service. No particular notification integration is required.
Entity-based `notify.send_message` targets are not supported by this service
selector. Notifications stay disabled until explicitly activated in the
operating-mode options. New installations do not need an imported checkpoint.
Observation-only samples are recorded without queuing notifications for later
activation.

The Windows/Wazuh source is optional and disabled by default. If enabled, enter
its SSH host and verify its host key. Install `gateway/configuration.py` beside
`gateway/windows_observer.py` and configure the Windows reader on that host:

```json
{
  "agent_id": "042",
  "database": "/var/ossec/queue/db/global.db",
  "alerts_root": "/var/ossec/logs/alerts",
  "maximum_archive_bytes": 1073741824
}
```

Save this as `/data/service-route-sync/windows.json`, owned by root with mode
0600, inside a root-owned directory with mode 0700. The reader selects only the
configured agent; the SSH caller cannot supply an agent ID or filesystem path.
Without this source, Windows event/heartbeat observations remain unavailable.
The daily archive limit defaults to 1 GiB and can be explicitly set up to 8 GiB.
Response pages remain bounded independently. Compressed archives replay their
prefix in bounded memory; large configured limits may require more CPU time.
Exceeding the limit reports an error and preserves the cursor; it never silently
skips historical events. Increase the configured bound for a larger day, rather
than resetting its cursor.

Install `gateway/configuration.py` beside the gateway WAN probe script too.
Configure `/data/service-route-sync/wan-probe.json` with your primary WAN
interface and packet mark, owned by root with mode 0600:

```json
{"interface": "wan0", "mark": 563347456}
```

These are examples, not autodetected values: use the interface and routing mark
from your own gateway. Both the route check and ping bind to those settings.
DNS resolvers are selected explicitly in the diagnostic DNS options.

Install the same configuration reader alongside `client_observer.py`. Both the
regular and single-profile diagnostic client observers read
`/data/service-route-sync/routes.json` to map routing marks to generic roles:

```json
{"primary": 563347456, "secondary": 563478528}
```

Use your own marks; `vpn1` and `vpn2` are optional additional keys. The file and
parent directory have the same root ownership and private permissions as the
other gateway configurations. Old `dsl`/`starlink` route labels are no longer
accepted by the client-observation protocol. Upgrade both sides together.

Set the expected LAN port speed in the diagnostic profile. Optional maintenance
suppression starts at the selected hour in HA's time zone and lasts the configured
number of minutes; hour `-1` disables it (the default).

Incident timestamps and maintenance calculations use Home Assistant's configured
time zone. No `ZoneInfo` filesystem lookup runs in the sampling loop.

## Routes and rollout

Fortnite can be bound to any eligible IP route. The integration preserves its
existing `ip_ranges`; it no longer requires two particular address ranges.
All other destination, removal, backup and readback safeguards remain in place.

Configure the new release in observation/preview mode first. This change is
validated with automated controller, Home Assistant and gateway tests; live
controller authentication and router writes still need validation in your own
setup before enabling automatic writes.

## Installation and updates

Version 0.5.1 uses configuration schema 2.1. Existing installations on this schema
keep their configuration, entity IDs, accepted cursors, history and operating
modes. Back up Home Assistant before updating. Automatic upgrades from earlier
experimental schemas are not supported; new users configure the integration
through its setup flow.

The gateway is a separate deployment. Monitoring uses the restricted command
`service-route-sync monitoring-client v1` with `monitoring_client_observer.py`
and `monitoring-client.json`. HA updates do not rewrite gateway SSH authorization
or privileged firewall state.

## Independent metrics and probes

Client metrics are available at `/api/service_route_sync/{entry_id}/metrics/clients`
without work probes or gateway probes. Work metrics use `/metrics/work` and require
an administrator. `/metrics/gaming` remains compatible for existing consumers;
its work metrics are included only for administrators. Monitoring uses
`/metrics/monitoring`; DNS source roles are `dns_primary` and `dns_secondary`.

For public HTTPS probes alone, leave the UniFi connection empty during setup and
select only that feature. To add controller-dependent features, create a separate
entry with the controller's own API key. Features remain optional; enabling SSH
observation does not enable route or IPv6 writes.

DNS monitoring accepts two explicit unicast IPv4/IPv6 resolver addresses and a
configurable query name (default `example.com`). Loopback, link-local, multicast,
unspecified and documentation addresses are rejected. The resolver ports and DNS
record type remain fixed; service callers cannot choose a destination.

IPv6 NETMAP requires the configured ULA and provider delegation to have the same
prefix length, between /48 and /64. Configure the observer's delegation length to
match. Interface names, tables and marks must be explicitly selected for the
installation. See [architecture and supported platform boundaries](ARCHITECTURE.md).
