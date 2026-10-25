# Restricted IPv6 policy actor

The IPv6 policy controller is separate from the read-only WAN observer and
from destination-route synchronization. It manages exactly the managed
`IOB_V6_NAT` and `IOB_SAB_PBR` chains, their two UniFi hooks and the source
policy at preference 31010. It never accepts a shell command, arbitrary
prefix, interface, mark, table, or rule from Home Assistant. The gateway keeps
the private ULA, source-routing IPv4 addresses and current delegated prefix.
HA receives only closed status flags and a SHA-256 digest of the managed state.

## Provisioning

1. Retain the existing read-only WAN observer. In the integration network
   options, enable IPv6 policy inspection; leave **Activate HA as the sole
   IPv6 policy writer** off. The flow creates a separate Ed25519 private key
   under HA's private configuration directory and shows only its public key.
2. Install `gateway/ipv6_policy.py` and `gateway/ipv6_actor.py` under the
   gateway's root-owned `/data/service-route-sync` directory (mode 0700).
   `ipv6_actor.json` must be root-owned 0600 with the exact deployment-specific
   ULA, marks, source-routing addresses and configured WAN interfaces and tables.
   The actor script must be root-owned 0700. Never put these values in HA options
   or diagnostics.
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
   intended policy and the real managed rule set over normal WAN changes.
5. At the supervised handover, stop and disable any other IPv6 writer. Confirm
   that no other process writes the managed chains. Only then enable the HA
   writer in the integration options. The first active refresh reads a fresh
   actor digest and WAN observation before any write.

## Gateway configuration

Use this schema for `/data/service-route-sync/ipv6_actor.json`. All addresses,
interfaces, tables and marks below are examples to replace with your values.
Primary and secondary roles are independent of provider names. The policy is
specifically a ULA NAT policy with matching ULA and delegated-prefix lengths
from /48 through /64: primary uses NETMAP and secondary uses masquerading.
The example below uses /56. Set the observer's `delegated_prefix_length` to
the same length. Different firewall layouts or policy types need a different
backend; they are not accepted by weakening the ownership checks.

```json
{
  "schema": 1,
  "ula_prefix": "fd12:3456:789a:bc00::/56",
  "primary_interface": "wan0",
  "secondary_interface": "wan1",
  "primary_table": "301.fiber",
  "secondary_table": "302.mobile",
  "primary_mark": "0x1a0000/0x7e0000",
  "secondary_mark": "0x1c0000/0x7e0000",
  "sab_primary_ip": "192.168.10.10",
  "sab_secondary_ip": "192.168.10.11"
}
```

HA options select the control helpers and map the input-select values to the
`primary` and `secondary` protocol roles. The `sab_primary_ip` and
`sab_secondary_ip` fields are the two distinct IPv4 source-routing addresses.
Interface names, table names and marks are validated before forming command
arguments. Provision reader and writer from the same release and use the
documented protocol roles on both sides.

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
An old primary-WAN delegated prefix is recognized separately from foreign rules;
a fresh authoritative prefix and healthy primary path allow an exact NETMAP update.
Missing hooks are recognized by the actor but do not imply an arbitrary policy
rewrite. Any extra rule, nonexact source, unfamiliar policy preference or
ambiguous partial state blocks automatic changes.

## Validation scope

Gateway tests exercise failures at mutation boundaries, delegation changes,
blank-state reconstruction, missing hooks and concurrent edits. A live check
in one installation confirms an active writer, a current primary-mode decision,
no review latch and working outbound IPv6. It does not establish compatibility
with other gateway firmware or firewall layouts. See [validation](VALIDATION.md).

## Routing marks

Use the mark and mask from the current gateway's `ip -4 rule show` entries for
the configured WAN tables. Both WAN marks must use the same mask and differ in
value. The actor uses that mask for both MARK and CONNMARK; it does not assume
an older UniFi mask. For example, a controller may use
`0x21940000/0x3ffe0000` and `0x21960000/0x3ffe0000`. These are examples, not defaults.
After controller reprovisioning, verify marks as well as interfaces, routing
tables and delegated prefixes. A completed historical journal does not prove
that the current rules still match; ambiguous partial states remain blocked
until audited and repaired.
