# Optional read-only gateway observations

This is an optional diagnostic feature. It does not install an IPv6 writer,
change router policy, or replace an existing failover controller. The HA flow
requires a discovered UniFi gateway with a numeric local address, generates its
own Ed25519 key and verifies a real protocol response before saving the entry.
Router-independent WAN telemetry remains usable if SSH observations fail.

## Administrator provisioning

Use an existing trusted administrator connection. Never use `ssh-keyscan` as
proof of identity or copy an unrestricted administrator private key into HA.
Confirm the gateway's Ed25519 host public key through that trusted connection.
The flow accepts the full `ssh-ed25519 <base64>` key, without comments, rather
than a fingerprint. Password authentication, forwarding and arbitrary shell
access are not needed for the observer key.

1. Enable protocol observations in the integration's network settings. The next
   step displays the newly generated **public** observer key; its private part
   remains under HA's configuration directory with private permissions.
2. Install the reviewed `gateway/observer.py` as
   `/data/service-route-sync/observer.py`. Its directory must be root-owned 0700,
   the script root-owned 0700, and `observer.json` root-owned 0600. Python 3.9+,
   OpenSSH, iproute2, iputils and the existing UniFi UDAPI tool are required.
3. Inventory the actual WAN interfaces and policy table numbers. Write this
   closed configuration, adapting both interfaces and tables to the gateway:

   ```json
   {
     "schema": 1,
     "primary_interface": "wan0",
     "secondary_interface": "wan1",
     "primary_table": 301,
     "secondary_table": 302,
     "delegated_prefix_length": 56
   }
   ```

   These are example values. When using the IPv6 actor, match its WAN settings
   and configured ULA/provider prefix length; the actor supports /48 through /64.

4. Add only the flow's public key to the gateway's existing authorized-key file,
   preserving other administrator keys. Constrain it to the actual HA address:

   ```text
   restrict,from="HA_ADDRESS",command="/usr/bin/python3 /data/service-route-sync/observer.py" ssh-ed25519 PUBLIC_KEY service-route-sync-observer
   ```

   Verify support for OpenSSH `restrict` and the effective authorized-key path
   on this firmware. Do not authorize this key without the forced command.
5. Enter the trusted host public key and the matching WAN interfaces in the HA
   flow. A valid response completes setup; authentication failures, mismatched
   interfaces, unexpected fields and stale/future samples retain the form with
   a sanitized error. No policy change occurs during the handshake.

The helper rejects any original SSH command except
`service-route-sync observe v1`. Raw UDAPI can contain PPPoE credentials; the
helper filters it **on the gateway**, returning only the closed observation
protocol. Delegated prefixes remain private internal data and are excluded from
entity attributes and integration diagnostics. Fixed external probes verify
their intended output route and use existing UniFi marks; no marks/rules are
created. Local probe setup errors are unknown, not confirmed outages.

## Lifecycle and maintenance

Observations refresh every 10 seconds. Samples older than 20 seconds or from
the future become unavailable; missing required facts also become unavailable.
The helper rate-limits calls to five seconds, bounds command duration/output,
and cleans up children on cancellation. SSH has pinned host identity, no agent,
password or proxy fallback, and bounded output. Changing only observer settings
does not disable route automation. Changes to the controller connection or route
policy require explicitly enabling route automation again.

Unchanged observer settings reuse the existing identity. Choosing reconfigure,
or changing the discovered gateway address, creates a separate key directory;
canceling cannot overwrite active trust material. Reconfigure requires
authorizing the new public key. After successful verification and cutover,
remove the superseded observer key through the trusted administrator connection.
Do not remove unrelated keys. Canceled setup directories can be reviewed and
removed after confirming their IDs are absent from active entries.

Gateway firmware upgrades, SSH policy changes or reboots require checking the
helper/configuration/authorized-key persistence. Failure results in unavailable
diagnostics, not a new routing decision. No firmware persistence hook or router
restart is installed by this integration. Optional write access uses the separate
[IPv6 actor](IPV6_ACTOR.md), with its own key and explicit activation.
