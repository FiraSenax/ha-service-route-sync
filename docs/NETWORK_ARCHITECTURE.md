# Network observations and policy control

For the package layout and shared security boundaries, see
[Architecture](ARCHITECTURE.md). This document describes the network-specific
behavior; [Validation](VALIDATION.md) records what has actually been tested.

## Independently scheduled features

Destination routes and local WAN telemetry use separate Home Assistant
DataUpdateCoordinators. A destination-feed outage does not suppress local WAN
measurements. Optional features declare their prerequisites in the composition
root.

A feature that cannot be constructed is reported in `feature_setup_issues` and
requires reconfiguration or reload. A constructed coordinator whose initial
observation fails remains available for polling retries when another feature
can load. If no selected coordinator becomes ready, HA retries entry setup.
Shutdown closes coordinators and owned transports; entities remove their
listeners on unload.

Client and WAN reads use this integration's own controller configuration and
local Network API key. Destination writes use an explicitly configured route
API key and certificate pin. Neither path uses another integration's client
or credentials. Certificate rotation stops pinned requests until the new pin
is independently verified and saved.

Public feeds have a 30-second total refresh budget. Failed route refreshes use
a one-minute retry interval and return to the configured interval after success.
An uncertain-write latch is retained; retrying observations does not authorize
another ambiguous write.

## Telemetry semantics

Only a literal boolean `up` means current WAN link state. Rolling `availability`
is a separate percentage, not current reachability or protocol health. Missing
and malformed fields are unknown. Real zero is preserved. Rates are reported
as decimal Mbit/s, converted once from the byte/s API field. Entity units are
set during construction, even when the initial state is unavailable.

Receipt freshness is bounded. Future or naive timestamps, stale observations,
a disconnected gateway and failed updates make the relevant entities
unavailable. A successful controller reply does not establish end-to-end
connectivity. WAN telemetry alone does not trigger failover; IPv6 policy control
uses separate protocol-specific observations.

Public IP addresses and raw device payloads are excluded from entity attributes
and integration diagnostics. API error bodies are sanitized before logging.
The restricted gateway reader filters raw UDAPI on the gateway, since those
responses can include credentials and other unrelated private data.

## Protocol health

The observer verifies the intended packet route using the configured interface,
table and existing UniFi mark before classifying external probe results. It
creates no routing rules or marks. Local probe setup errors remain unknown
health rather than a confirmed remote outage.

A fresh, explicit carrier-down observation can report a protocol as down when
its route or probe setup is absent. A successful probe contradicting carrier-down
remains unknown; an unknown carrier with a failed probe also remains unknown.
Positive IPv6 health additionally requires the expected address, delegation
and default-route evidence.

## IPv6 controller

The pure planner performs no I/O. It requires fresh protocol-specific health,
an exact recognized router state, continuous elapsed-time hysteresis and explicit
enablement. Unknown observations reset hysteresis. A restart rediscovers actual
router state and resets continuity. Both WANs being down never authorizes dropping
policy rules or leaking managed traffic.

A separate SSH key authorizes only inspection and fixed policy requests to the
restricted actor. The actor verifies ownership of its exact chains, hooks and
source policy. It uses a durable intent, a digest comparison before writing,
exact readback after mutation, bounded rollback and a persistent review latch
for ambiguous results. The read-only observer remains independent.

The current actor supports the documented UbiOS layout, with matching ULA and
provider prefix lengths from /48 through /64. It is not a generic firewall
backend. Interfaces, tables, marks, helper entities and WAN preference values
must be configured for the installation. Provisioning and recovery are described
in [IPv6 actor](IPV6_ACTOR.md); the required privilege boundary is documented in
[Permissions](PERMISSIONS.md).

Destination-route automation and IPv6 actuation are separate opt-in controls.
Enabling an observer does not activate either writer. Check the current HA state
and gateway rules on each deployment; a past successful check is not evidence
that rules still match after a reboot or controller reprovisioning.
