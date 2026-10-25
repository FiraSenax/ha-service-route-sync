# Client monitoring

Client profiles, MAC aliases and routing roles are explicitly configured. See [public configuration](CONFIGURATION.md) for connection settings, optional diagnostics and gateway routing-mark configuration.

## Gateway boundary

`gateway/client_observer.py` exposes one fixed original SSH command: `service-route-sync clients v1`. Install alongside `observer.py` and `configuration.py` in a root-owned private directory only after a backed-up deployment review. Use a dedicated client-observer SSH identity with `restrict`, `from` limited to the HA address, and a forced command invoking the fixed absolute helper path. Do not extend the existing WAN observer key to accept arbitrary commands or target arguments. Pin the gateway host key in HA through the existing trusted connection.

The helper reads `/data/service-route-sync/clients.json`, with exact structure:

```json
{"schema":1,"targets":{"example":{"address":null,"macs":["02:00:00:00:00:01"]}}}
```

Both the containing directory and file must be root-owned and private (0700 directory, 0600 file). The file is opened without following symlinks, bounded to 16 KiB, and validated. Targets contain canonical unicast MAC aliases and either an explicit private IPv4 address or null for local neighbor-cache resolution. A null target resolves only when exactly one usable local neighbor matches its approved aliases. Missing or ambiguous matches retain the profile with unknown measurements. Duplicate IPs and aliases across profiles are rejected. The SSH caller cannot supply arbitrary commands or target identities. The bounded address-selection request described below is accepted only after checks against the gateway-owned profile and neighbor identity.

Neighbor IP/MAC identity is checked before and after bounded ping and conntrack reads. Unknown identity yields no probe; a changed identity discards all results. This protects attribution against ordinary DHCP changes but is not cryptographic client authentication. An offline machine without a usable neighbor entry remains unknown to this probe. UniFi presence is a separate observation. DHCP reservations and profile aliases must be checked before installation.

An exclusive private runtime lock prevents concurrent scans and limits starts to one per five seconds. Each subprocess has a deadline and bounded output and is killed on cancellation. The whole scan is bounded to 55 seconds for up to sixteen targets with four workers. The dedicated client transport accommodates this bound; larger configurations may take longer than five seconds to sample.

Only reduced results leave the gateway. Raw conntrack lines, neighbor MACs and addresses are not returned. Source timestamps are emitted only for successful observations. Failed command reads yield missing data; successful empty conntrack output yields real zero counts. Configure routing marks for the generic primary, secondary and optional VPN roles and verify them against the gateway's actual routing policy.

## Setup and verification

Select clients through the normal Home Assistant configuration flow. Optional SSH probes require their own key and a verified live handshake. The authenticated client metrics endpoint can run without work probes or WAN monitoring. Automated tests cover the protocol and identity checks; verify SSH restrictions and target identity on each deployment. See [configuration](CONFIGURATION.md) and [permissions](PERMISSIONS.md).

## Active adapter selection

The native UniFi client collector selects the freshest active adapter of each existing profile. The client SSH transport can pass its selected IPv4 addresses as a bounded JSON stdin frame, never as a command or argument. The gateway accepts only its configured profile set and RFC1918 addresses; both pre- and post-measurement neighbor checks must match a MAC from the gateway-owned profile. Fixed-address profiles cannot be widened. Offline or failed/stale UniFi observations send null and do not fall back to historical neighbors.

The optional request is `{"schema":1,"addresses":{"example":"192.168.1.10"}}`. Empty stdin retains the existing gateway-only resolution mode for provisioning and backwards compatibility. Reading stdin is bounded to 16 KiB and two seconds; duplicate JSON keys are rejected. This authorizes an address through a configured MAC identity rather than trusting the proposed address. It does not authorize arbitrary LAN or public probes.

Both HA and the gateway validate active adapter selection. Public targets are rejected; a foreign local MAC produces unknown with no probe or connection measurements. Empty-input gateway-only resolution remains available for provisioning.
