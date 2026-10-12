# Network extension: design and acceptance

## Independently scheduled features

Existing destination routes and local WAN telemetry use separate HA
DataUpdateCoordinators. A destination feed outage must not suppress local WAN
measurements. If one feature cannot initialize, the other loads and the failed
feature remains unavailable and retries through its entity listeners. If all
selected features fail, HA retries entry setup. The HA coordinator registers
its shutdown with the config entry; entities remove their listeners on unload.

The official UniFi parent integration remains the source for client and WAN
reads and for selecting the controller. It has read-only route permission on
this installation. Destination route reads and writes therefore use a separate
dedicated API key stored in the HA config entry, never exposed in diagnostics.
Those requests use fixed traffic-route paths, HTTPS certificate SHA-256 pinning,
no redirects, bounded timeouts and sanitized error bodies. A gateway must be
explicitly selected from discovered WAN-capable devices. Certificate rotation
stops route updates until the pin is revalidated in the options UI.

## Telemetry semantics

Only a literal boolean `up` means current WAN link state. Rolling `availability`
is a separate percentage, not current reachability or protocol health. Missing
and malformed fields are unknown. Real zero is preserved. Rates are reported
as decimal Mbit/s, converted once from the byte/s API field. Entity units are
set during construction, even when the initial state is unavailable.

Receipt freshness is bounded. Samples from the future, naive timestamps, stale
samples, a disconnected gateway and failed updates make entities unavailable.
A successful controller reply is not proof of fresh hardware measurements or
end-to-end connectivity. The first implementation deliberately does not use
telemetry as input for router actuation. Public IP addresses and raw device
payloads are not entity attributes or diagnostics. API exception bodies are
sanitized before they reach HA logging.

## IPv6 controller

The pure planner has no I/O. It requires independently verified protocol-
specific health, an exact existing router state, continuous elapsed-time
hysteresis and explicit enablement. Unknown observations reset the hysteresis.
A restart rediscovers actual router state and resets continuity. A both-down
result never authorizes dropping policy rules or leaking managed traffic.

The deployed restricted gateway actor owns only the legacy IPv6 NAT and policy
chains. A separate HA-origin-restricted SSH key requests inspection or one of
two fixed target modes; arbitrary commands and parameters are denied. It uses
authoritative DHCPv6 delegation discovery, interface-bound probes, a pinned
host identity, durable intent, compare-and-swap digest, exact readback after
every mutation, bounded rollback and a persistent latch on ambiguous results.
The read-only WAN observer remains independent. The former VM101 service was
disabled before HA became the sole writer and VM101 has been removed.

### Writer deployment, 2026-10-12

A real HA-originated DSL→Starlink→DSL exercise completed with exact readback
and closed gateway journals. HA reports DSL, a current decision and no review
latch. Gateway tests inject failures at every mutation boundary for both WAN
switches, delegated-prefix rotation, blank gateway reconstruction and missing-
hook repair. Concurrent edits and changed delegation stop writes. See
[IPV6_ACTOR.md](IPV6_ACTOR.md) for the recovery procedure and the private
migration record for the verified pre-cutover rule snapshot and PBS restore.

Automatic route updates use a separate dedicated UniFi route API key and
pinned certificate. Eight route policies were checked after cutover; the
automatic update switch is on and its status is current. Route changes do not
share the IPv6 writer or its credentials.

## Tests

Router-independent suite: `python -m unittest discover -s tests -v`.
Real HA runtime suite: Python 3.14, Home Assistant 2026.10.0, aiounifi 95,
`python -m unittest discover -s tests_ha -v`. It covers entity units and zeroes,
stale/future data, unavailable/recovery, unload, partial feature setup, old
route-only entries and read-only flow validation. Both suites run in CI.

## Findings from the live gateway, 2026-10-11

`ubios-udapi-client GET /interfaces` reports active DHCPv6 delegations in
`ipv6.dhcp6PDStatus[].network`. This is independent of rotated log files. Raw
responses also include PPPoE credentials: filtering must happen inside the
forced-command gateway helper, before anything is sent to HA. The new closed
observation schema rejects unexpected fields and rejects private/unsupported
prefixes, ambiguous interfaces, nonboolean results and stale/future timestamps.

An interface-bound ping alone did not validate Starlink IPv6. Its unmarked
route lookup failed locally, despite an existing secondary routing table. A
lookup using the existing UniFi WAN mark and the intended output interface
verified table 202 / eth1, and both external IPv6 probes then succeeded. The
helper must verify the selected packet route before classifying probe failures;
invalid commands and local probe setup errors are unknown health. No routing
rule was changed for these tests.

The timestamp sensor and improved cloud-error recovery have additional real HA
tests. Cloud reads have a 30-second total feed budget, recover on a one-minute
interval after failure, and return to the configured interval after success.
An uncertain-write latch is retained during retries; blocked writes are never
retried automatically. These refinements were deployed with the separately enabled read-only observer
on 2026-10-11. HA restart, fresh native WAN timestamps and all four IPv4/IPv6
connectivity entities were verified. Automatic route updates are active after dedicated credential and TLS pin verification.
The observer key rejected arbitrary and write commands in real HA-originated
SSH checks; the HA IPv6 controller is now the sole writer. See
[GATEWAY_OBSERVER.md](GATEWAY_OBSERVER.md) for provisioning and lifecycle.

## Confirmed carrier loss and contradictory observations

A fresh explicit carrier-down observation is sufficient to report that protocol
as down even when its route or probe setup has disappeared. A successful probe
contradicting carrier-down remains unknown; an unknown carrier with a failed
probe also remains unknown. Prefix/address/default evidence is still required
for positive IPv6 health. Unknown is never converted to a guessed outage.
Three pure regression cases and a real HA entity test cover this boundary.
This refinement is deployed to HA.

## Native controller capability check before writer implementation

The live gateway reports UniFi Network 11.0.83-37119-1. Current vendor
documentation supports IPv6 NAT66 with ULA addresses, including interface-bound
masquerading. Before building a custom root writer, inspect native NAT policy
and routing capabilities, including protocol support, source selection and
multi-WAN failover behavior. NAT66 support alone does not prove automatic IPv6
failover or equivalence to the old prefix NETMAP behavior. No native policy has
been created or altered during this capability check.

Primary sources:
- https://help.ui.com/hc/en-us/articles/36378535649687-Configuring-IPv6-in-UniFi
- https://help.ui.com/hc/en-us/articles/16437942532759-DNAT-SNAT-and-Masquerading-in-UniFi
- https://help.ui.com/hc/en-us/articles/12566175125783-UniFi-Gateway-Policy-Based-Routing
