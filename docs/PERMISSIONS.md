# Permissions and least privilege

Service Route Sync authenticates only with explicitly configured **local UniFi
Network API keys**. It never requests a UniFi username, password or TOTP secret,
and never uses another Home Assistant integration's credentials or client.
Create a dedicated key for this integration in your controller's Integrations
settings. Keep it in the integration's configuration, not in public YAML,
screenshots, issue reports or repository files.

Ubiquiti distinguishes local Network APIs from the cloud Site Manager API;
local API documentation is available in the installed Network application's
Integrations settings. Consult that version's documentation when assigning
permissions: [official UniFi API guide](https://help.ui.com/hc/en-us/articles/30076656117655-Getting-Started-with-the-Official-UniFi-API).

## Required operations

These are the operations the integration needs, **not names of UniFi permission
scopes**. This implementation uses UniFi OS compatibility endpoints, rather than
`/integration/v1`; permission behavior for another API is not proof that these
endpoints enforce the same restrictions.

| Feature | Controller operations | Credential |
| --- | --- | --- |
| Client, Wi-Fi and network observations | `GET /proxy/network/api/s/{site}/stat/sta` and `/stat/device` | Connection API key |
| Route discovery and preview | `GET /proxy/network/v2/api/site/{site}/trafficroutes` | Route key if configured, otherwise connection key |
| Apply or restore route destinations | Route reads plus `PUT /proxy/network/v2/api/site/{site}/trafficroutes/{id}` | Explicit route API key |

No controller user management, firmware updates, route creation or route deletion
is required. Route writes additionally require the verified certificate pin and
the integration's write safeguards. Preview mode performs no route writes.

## What least privilege means here

Select the narrowest permissions that your controller version actually enforces
for the required endpoints. Observation-only installations need reads; writers
also need updates to the selected routes. Do not grant unrelated administrative
rights merely because this integration exists.

A key is **not proven read-only** because observations succeed, and two separate
keys do not by themselves provide permission separation. This project does not
claim that UniFi offers route-specific or read-only scopes for these endpoints.
If your controller cannot enforce the desired restrictions, the key may retain
broader privileges than the operations listed above. Decide whether that access
is acceptable before enabling the integration; its application-level safeguards
do not reduce the key's controller-side privileges.

Use separate observation and route keys when available, especially if the
controller supports enforceable restrictions. Separate keys still help rotation
and revocation if their permissions are identical. Do not temporarily elevate an
account to manufacture a supposedly restricted key through undocumented behavior.
Verify restrictions using the controller's documented permission model or a test
controller, not speculative mutation requests against production routes.

Restrict network access to HTTPS from Home Assistant to the selected controller.
Use trusted TLS or an independently verified certificate fingerprint; never
disable certificate validation. Revoke unused keys and update the integration
when rotating a key. Treat HA configuration and backups as sensitive: the keys
must remain recoverable by HA and are not a substitute for encrypted backups.

## Optional SSH features

Enable only the observers and actors you use. Provision a separate SSH key per
capability, restrict it to the HA source address, pin the host key and use a fixed
forced command with no interactive shell, forwarding, agent forwarding or PTY.
The caller must not be allowed to supply arbitrary filesystem paths or commands.

Gateway observations need the specific local network data and probes implemented
by their scripts. IPv6 actuation additionally needs privileged routing/firewall
operations; the forced command constrains the caller but does not make those
operations unprivileged. Keep scripts and configuration root-owned and inaccessible
for modification by the SSH caller. See [IPv6 actor provisioning](IPV6_ACTOR.md)
and [configuration](CONFIGURATION.md) for the matching release's configuration.
The optional Windows reader needs access only to the configured Wazuh agent's
source files through its fixed reader command.

## HA authorization and updates

Route switches and buttons (including refresh) require an administrator or a
trusted HA automation context, since refreshing may trigger enabled writes.
Ordinary entity-control permission is insufficient. API callers cannot assign
their own trusted automation context. Work-probe metrics also require an admin;
client metrics use their entity read permissions.

Upgrade migrations do not provision or broaden controller/SSH permissions.
Schema-1 upgrades pause external writers; schema-2 upgrades preserve existing
modes. Review the documented platform requirements before granting gateway access.
