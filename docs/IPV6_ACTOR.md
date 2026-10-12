# Restricted IPv6 policy actor

The IPv6 policy controller is separate from the read-only WAN observer and
from destination-route synchronization. It manages exactly the legacy
`IOB_V6_NAT` and `IOB_SAB_PBR` chains, their two UniFi hooks and the source
policy at preference 31010. It never accepts a shell command, arbitrary
prefix, interface, mark, table, or rule from Home Assistant. The gateway keeps
the private ULA, SAB addresses and current delegated prefix. HA receives only
closed status flags and a SHA-256 digest of the managed state.

## Provisioning

1. Retain the existing read-only WAN observer. In the integration network
   options, enable IPv6 policy inspection; leave **Activate HA as the sole
   IPv6 policy writer** off. The flow creates a separate Ed25519 private key
   under HA's private configuration directory and shows only its public key.
2. Install `gateway/ipv6_policy.py` and `gateway/ipv6_actor.py` under the
   gateway's root-owned `/data/service-route-sync` directory (mode 0700).
   `ipv6_actor.json` must be root-owned 0600 with the exact deployment-specific
   ULA, marks, SAB addresses and fixed WAN tables. The actor script must be
   root-owned 0700. Never put these values in HA options or diagnostics.
3. Using an existing trusted gateway administrator connection, add the public
   key to the effective `authorized_keys` with an HA-source restriction and
   a forced command:

   ```text
   restrict,from="HA_ADDRESS",command="/usr/bin/python3 /data/service-route-sync/ipv6_actor.py" ssh-ed25519 PUBLIC_KEY service-route-sync-ipv6
   ```

   The actor accepts only original command `service-route-sync ipv6 v1` and a
   closed JSON request. Authorize no shell, forwarding, agent, or password
   fallback. Verify the gateway Ed25519 host key through the trusted connection;
   the HA setup compares it with the observer's pinned identity and performs
   an actual read-only inspection.
4. Confirm that HA reports an exact mode and fresh primary/secondary IPv6
   observations. While inspection-only, compare HA's planned target with the
   legacy controller and the real managed rule set over normal WAN changes.
5. At the supervised handover, stop and disable the VM101 IPv6 writer. Confirm
   that no other process writes the managed chains. Only then enable the HA
   writer in the integration options. The first active refresh reads a fresh
   actor digest and WAN observation before any write.

## Failure and recovery

The gateway writes a root-private, fsynced intent before modifying rules and
marks it complete only after an exact readback. HA also durably saves its own
intent before sending an apply request. A timeout, lost response, invalid
readback, crash during intent, or ambiguous rollback latches further HA writes.
The diagnostic **IPv6 policy review required** sensor reports this condition.
There is intentionally no automatic retry or one-click latch reset. Stop the
HA writer in the integration options and inspect the root-private gateway
journal plus the exact NAT, mangle and policy rules through a trusted admin
connection. If a gateway intent is incomplete, restore only the verified
owned rule set from the pre-change snapshot and mark the gateway journal
complete with the local-root-only `gateway/ipv6_reconcile.py` after an exact
readback. It requires the recorded transaction ID plus the observed digest and
mode, and rejects a foreign rule or mismatched journal. An unfamiliar rule or
changed delegation requires manual network review. Once gateway `inspect` reports
`review_required=false`, an HA administrator can call
`service_route_sync.acknowledge_ipv6_policy_review` with the current mode and
the exact digest returned by `inspect`. The service accepts only the original
digest or the recorded target mode, requires the HA writer to be inactive and
atomically closes the HA intent. Re-enabling or restarting HA alone does not
clear it.

If the gateway reboots and the managed state is exactly blank, HA can rebuild
it only after at least 15 seconds of continuously fresh, healthy WAN evidence.
An old DSL delegated prefix is recognized separately from foreign rules; a
fresh authoritative prefix and healthy DSL path allow an exact NETMAP update.
Missing hooks are recognized by the actor but do not imply an arbitrary policy
rewrite. Any extra rule, nonexact source, unfamiliar policy preference or
ambiguous partial state blocks automatic changes.

## Status

On 2026-10-12 the writable restricted actor became the sole IPv6 policy
writer. The former VM101 service was stopped and disabled before activation;
VM101 has since been removed. The actor checks the exact managed state after
every mutation and latches on a concurrent edit, changed delegation or
ambiguous rollback. A real HA-originated DSL→Starlink→DSL exercise completed
with exact readback and closed journals. HA reports DSL, a current decision
and no review latch. The read-only WAN observer remains independent. The
pre-cutover rule snapshot, PBS restore record and recovery details are in
`/home/sven/iobroker-host-exit-2026-10-11/VM101_RESTORE_AND_IPV6_CUTOVER.md`.
