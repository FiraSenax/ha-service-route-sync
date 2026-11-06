# Releases and repository protection

## Compatibility checks

The `Tests` workflow runs on pull requests, pushes, daily at 05:17 UTC, and on
manual dispatch. Each run reads the exact minimum HA release from `hacs.json`
and the latest stable `homeassistant` release from the official PyPI JSON API.
It derives the appropriate Python series from each release's `Requires-Python`
metadata and uses its latest patch. Beta, development and yanked releases are
rejected. An unavailable minimum or failed metadata lookup fails the workflow;
there is no fallback to an older cached latest version.

Both endpoints run the unit, gateway and real HA runtime suites. If minimum and
latest are the same release, one job is labelled `minimum/latest`. Intermediate
HA releases are not covered by this endpoint matrix. The versions and Python
series actually selected are visible in the metadata job and matrix job names.

Each HA environment is installed with HA's own dependency constraints. Its exact
installed package versions are audited with `pip-audit` from a separate virtual
environment. A vulnerability or audit error fails CI. Do not upgrade HA-owned
transitive dependencies independently to make an audit pass: use a fixed HA
release and explicitly reconsider the declared minimum when necessary.

`requirements-test.txt` pins a reproducible local development environment; it
does not determine the latest endpoint. Test tools and the audit tool have
separate requirement files and weekly Dependabot updates.

The stable `CI` check requires every test job, HACS, Hassfest, frontend/browser
checks, workflow validation and both HA endpoints to pass. CodeQL runs separately
on pull requests and main, and is also required by the release workflow.
The CodeQL job directly examines its SARIF results and fails for security severity
7.0 or higher and error-level findings. Missing/malformed results also fail the
job, so the release gate does not rely on scanner execution status alone.

## Preparing a release

1. Create a release PR from current `main`.
2. Update `custom_components/service_route_sync/manifest.json` with a new stable
   version such as `0.5.2`. Add the same version as the first versioned section
   of `CHANGELOG.md`, with user-facing changes and any upgrade instructions.
3. Update README version references and dated validation evidence as needed.
   Change the HA minimum only deliberately; CI reads it directly from `hacs.json`.
4. Wait for `CI` and `CodeQL`, review changes and security alerts, then merge.

There is no integration build step: HACS installs the files under
`custom_components/service_route_sync` from the published GitHub release tag.
The existing JavaScript and translations are already included there. Gateway
helpers remain separately provisioned; publishing a release grants no new HA
or gateway privileges. Do not add `zip_release` unless packaging changes.

## Validating and publishing on GitHub

Open **Actions → Release → Run workflow**, select **main**, and enter the
manifest version. Leave **Publish the GitHub release after all checks pass**
unchecked for a validation-only run. This can also validate the already
published manifest version without modifying a release.

For a new version, check that option to publish after validation. The workflow
reruns the same reusable tests, dynamically resolving minimum/latest, and
CodeQL on its own exact commit. Only the final publishing job has repository
contents write permission. It checks all of the following before creating a tag:

- The requested version, manifest and first versioned changelog section match.
- All required test and security workflows succeeded.
- `main` still points at the tested commit and is the default branch.
- The tag does not already exist and the version is newer than the latest release.

It creates a new `v<version>` tag at the exact tested SHA, then publishes a stable
GitHub release using that changelog section. Existing versions are never moved
or overwritten. If main moved, rerun validation on the new main commit. If a
network/API failure occurs after tag creation, inspect the tag and release on
GitHub before recovery; do not move the existing tag or blindly retry publication.

HACS requires a published GitHub release to advertise a new release version;
pushing a tag alone is insufficient. HACS itself can be public/read-only and
does not need credentials from these workflows.

## Repository settings

Configured on 2026-11-06: an active main ruleset requires PRs, current `CI`
and `CodeQL` results from GitHub Actions, resolved conversations, and CodeQL
results without high/critical security alerts or errors. It blocks deletion
and force pushes, with no bypass actors. Approvals are zero for this single-
maintainer repository. A separate active `v*` ruleset blocks tag updates,
deletion and force pushes while allowing fresh release tags to be created.
Private vulnerability reporting, dependency graph, Dependabot/malware alerts,
secret scanning and push protection are enabled.

The minimum is now **2026.11.0**. The previous 2026.9.0 endpoint passed all 445
functionality tests but its HA-owned packages produced 69 advisory entries in
five packages. The current 2026.11.0 environment passed the audit. This decision
raises the support floor instead of overriding HA's pins or ignoring advisories.

Workflow checks and `CODEOWNERS` are committed configuration. GitHub branch/tag
rules and account security are separate server settings and must be configured
by a repository administrator:

- Protect `main`: require pull requests, current successful `CI` and `CodeQL`
  checks from GitHub Actions, resolved review conversations, and block force
  pushes and branch deletion. Require code-owner review when a second maintainer
  is available; a sole maintainer cannot approve their own PR.
- Protect `v*` tags from updates and deletion. Allow the release workflow to
  create new tags; do not block the GitHub Actions integration it relies on.
- Enable Dependabot alerts/security updates, secret scanning and push protection
  where available. Use read-only default workflow token permissions.
- Prefer immutable releases where available. Avoid personal access tokens for
  publishing; the workflow uses its short-lived `GITHUB_TOKEN`.

No workflow changes HA's package constraints, integration runtime privileges,
gateway permissions, or HACS download access.

References: [HACS versions](https://www.hacs.xyz/docs/publish/start/#versions),
[reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows),
[GitHub Actions security](https://docs.github.com/en/actions/reference/security/secure-use).
