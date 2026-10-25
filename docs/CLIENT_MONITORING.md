# Client monitoring

Client profiles, MAC aliases and routing roles are explicitly configured. See [public configuration](CONFIGURATION.md) for connection settings, optional diagnostics and gateway routing-mark configuration.

## Gateway boundary

`gateway/client_observer.py` exposes one fixed original SSH command: `service-route-sync clients v1`. Install alongside `observer.py` and `configuration.py` in a root-owned private directory only after a backed-up deployment review. Use a dedicated client-observer SSH identity with `restrict`, `from` limited to the HA address, and a forced command invoking the fixed absolute helper path. Do not extend the existing WAN observer key to accept arbitrary commands or target arguments. Pin the gateway host key in HA through the existing trusted connection.

The helper reads `/data/service-route-sync/clients.json`, with exact structure:

```json
{"schema":1,"targets":{"example":{"address":null,"macs":["02:00:00:00:00:01"]}}}
```

Both the containing directory and file must be root-owned and private (0700 directory, 0600 file). The file is opened without following symlinks, bounded to 16 KiB, and validated. Targets contain canonical unicast MAC aliases and either an explicit private IPv4 address or null for local neighbor-cache resolution. A null target resolves only when exactly one usable local neighbor matches its approved aliases. Missing or ambiguous matches retain the profile with unknown measurements. Caller-selected addresses remain forbidden. Duplicate IPs and aliases across profiles are rejected. No addresses or commands are supplied by the SSH caller.

Neighbor IP/MAC identity is checked before and after bounded ping and conntrack reads. Unknown identity yields no probe; a changed identity discards all results. This protects attribution against ordinary DHCP changes but is not cryptographic client authentication. An offline machine without a usable neighbor entry remains unknown to this probe. UniFi presence is a separate observation. DHCP reservations and profile aliases must be checked before installation.

An exclusive private runtime lock prevents concurrent scans and limits starts to one per five seconds. Each subprocess has a deadline and bounded output and is killed on cancellation. Whole scan is bounded to 55 seconds for up to sixteen targets with four workers. HA must use a dedicated transport deadline allowing this bound, rather than the existing WAN observer transport timeout. The future scheduler must not promise a five-second cadence for configurations whose scan duration exceeds it.

Only reduced results leave the gateway. Raw conntrack lines, neighbor MACs and addresses are not returned. Source timestamps are emitted only for successful observations. Failed command reads yield missing data; successful empty conntrack output yields real zero counts. DSL/Starlink/VPN route marks preserve the actual old implementation and require a live routing-policy check on deployment.

## Outstanding acceptance

Closed HA protocol parser, dedicated pinned SSH transport, actual live measurements, HA runtime/UI installation, fixed HTTPS work probes, complete Monitoring incident/Windows/history/outbox migration, authenticated scrape endpoint and NAS config/history continuity remain pending. Current helper entrypoint tests prove code behavior, not installed SSH restrictions or live target identity.

## Active adapter selection (local candidate)

The native UniFi client collector selects the freshest active adapter of each existing profile. The client SSH transport can pass its selected IPv4 addresses as a bounded JSON stdin frame, never as a command or argument. The gateway accepts only its configured profile set and RFC1918 addresses; both pre- and post-measurement neighbor checks must match a MAC from the gateway-owned profile. Fixed-address profiles cannot be widened. Offline or failed/stale UniFi observations send null and do not fall back to historical neighbors.

The optional request is `{"schema":1,"addresses":{"example":"192.168.1.10"}}`. Empty stdin retains the existing gateway-only resolution mode for provisioning and backwards compatibility. Reading stdin is bounded to 16 KiB and two seconds; duplicate JSON keys are rejected. This authorizes an address through a configured MAC identity rather than trusting the proposed address. It does not authorize arbitrary LAN or public probes.

The active adapter selection is installed on HA and the gateway. A live check confirms successful periodic acquisition and rejection of public targets; a foreign local MAC produces unknown with no probe or connection measurements. Empty-input gateway-only resolution remains available for provisioning.
