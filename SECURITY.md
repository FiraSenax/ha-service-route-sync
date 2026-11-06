# Security policy

Please report potential vulnerabilities privately through the repository's
GitHub **Security → Report a vulnerability** feature when available. Do not post
API keys, SSH private keys, HA backups or raw Windows event payloads in issues.
If private reporting is unavailable, open an issue requesting a private contact
without including exploitation details or sensitive data.

Security fixes target the latest release. Installations with older versions should
upgrade using the documented installation procedure. See [security boundaries and
platform limitations](docs/ARCHITECTURE.md) and [required permissions](docs/PERMISSIONS.md).

CI includes extended CodeQL analysis, negative authorization/parser tests, pinned
GitHub Actions and generated-reader consistency checks. Every compatibility run
tests the declared minimum and dynamically resolved latest stable Home Assistant
release. Installed dependencies are audited without changing Home Assistant's
own pins. Workflow policy checks enforce read-only defaults, SHA-pinned Actions,
credential-free checkouts and the release gates. Only the final publishing job
receives contents write permission. See [the release process](docs/RELEASING.md)
for its checks and the separate administrator-managed repository protections.
These controls do not
replace review of the gateway deployment, API-key privileges or HA backup security.
The [validation record](docs/VALIDATION.md) distinguishes automated checks, live
evidence and work still needed, including coverage measurement and static type
checking. A clean CodeQL result is not an independent security audit.
