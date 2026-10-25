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
GitHub Actions and generated-reader consistency checks. These controls do not
replace review of the gateway deployment, API-key privileges or HA backup security.
