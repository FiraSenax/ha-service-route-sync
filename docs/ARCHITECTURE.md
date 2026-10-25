# Architecture and security boundaries

The integration is a composition of optional capabilities, not a requirement to
install the author's network layout. A controller connection is required only by
features that read UniFi. Public endpoint probes can run as a standalone entry.
The project remains a UniFi integration: it is not a universal router driver.

| Package | Responsibility | External access |
| --- | --- | --- |
| `routing` | Normalize feeds, plan updates, backup/verify/restore | Controller backend and approved HTTPS feeds |
| `network` | WAN observations and failover decisions | Controller or restricted gateway observer |
| `clients` | Explicit client identity and optional reachability | Controller, optional client probe command |
| `monitoring` | Client incident evaluation, history, optional notifications | Composed observations; configured notify service |
| `wifi` | Wireless observations and private history | Controller; admin-only history API |
| `ipv6` | Guarded policy decisions and transactions | Separate restricted gateway writer |
| `frigate` | Optional observation and recovery | Separate restricted peer command |
| `probes` | Public HTTPS endpoint measurements | Fixed public HTTPS destinations |
| `windows` | Bounded event/cursor protocol | Optional Wazuh reader |
| `backends` | HTTP/SSH transport, validation and deadlines | Fixed endpoint/command per capability |
| `flows` | Independent feature setup and option steps | Read-only setup handshakes |

`__init__.py` is the composition root. `lifecycle.Feature` declares prerequisites;
`RuntimeData.coordinators()` is the single cleanup inventory. A failed optional
factory is reported in diagnostics and cannot disable an independent feature.
A failed initial observation retains its coordinator for normal polling retries.
Setup failures that prevent creating a coordinator require reconfiguration/reload.

The pure route planner depends on a protocol instead of Home Assistant or a
particular controller client. Transport adapters do not accept commands from
service requests. Feature packages share typed observations and the transport
layer; they are not independent processes or security sandboxes.

## Trust boundaries

- All code in a custom integration runs with Home Assistant's privileges. Folder
  separation does not isolate secrets from another malicious HA integration.
- API keys belong to this entry. TLS is verified or explicitly certificate-pinned;
  redirects and environment proxies are disabled for authenticated requests.
- Controller JSON is limited to 4 MiB of decoded content and 64 nesting levels;
  parsing runs outside the event loop. SSH frames have independent tighter bounds.
- Route switches/buttons require an administrator or a trusted HA automation
  context. Network writes also retain preview validation, a persistent write latch,
  backups, concurrent-change checks and post-write verification.
- Client metrics require entity read permissions. Work-probe metrics, which have
  no equivalent entity permission scope, require an administrator. Monitoring
  metrics include WAN bandwidth only for administrators.
- Imports and privileged recovery services use HA admin authorization. History
  access is admin-only and bounded. Diagnostics omit keys, raw route payloads and
  private event bodies.
- Gateway credentials are separate per capability. Forced commands, host-key
  pins, source-address restrictions and root-owned scripts/configuration are part
  of the deployment contract. See [Permissions](PERMISSIONS.md).

Read-only and write keys are useful for revocation, but enforceable least privilege
depends on the controller. The UniFi compatibility API is not assumed to provide
route-specific authorization. The UbiOS IPv6 actor needs privileged network
operations; a forced command constrains its entry point, not its process UID.

## Shared protocol source

`windows/` is the canonical reader/protocol implementation. Run
`python scripts/sync_windows_reader.py` after editing it. The standalone copy in
`gateway/windows_reader` is generated; CI runs `--check` to prevent divergence.

## Portability and unsupported environments

The HTTPS backend currently supports UniFi OS compatibility API paths. A controller
with only `/integration/v1`, a different route schema or incompatible permissions
is rejected by setup; an API key alone is not a compatibility guarantee.

Public HTTPS phase probes require `/usr/bin/curl`; user curl configuration is
disabled and response bodies are limited to 1 MiB. SSH features require OpenSSH in the HA runtime and a gateway supporting the exact
reader's Linux/UbiOS tools. IPv6 writes additionally require the documented chain
layout, matching ULA/provider prefix lengths (/48–/64), explicit interface/table/
mark configuration and exclusive ownership of the managed policy. A different
firewall implementation needs a new backend, not weaker ownership checks.

Windows/Wazuh, Frigate, notifications, SSH probes and automated writes are optional.
The Windows adapter supports its documented Wazuh schema; it is not a general
Windows event collection service. Public endpoint probes use explicit shipped
presets and do not offer arbitrary request URLs.

Tests cover unrelated profile names, addresses, interfaces, prefix sizes, disabled
features, denied callers, malformed responses and upgrade conflicts. This is
compatibility evidence, not a claim to have tested every controller or firmware.
