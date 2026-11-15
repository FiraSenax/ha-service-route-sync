"""Validate and publish an immutable-version HACS release from a tested commit."""

import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def version_tuple(version):
    if not VERSION.fullmatch(version):
        raise ValueError("Use a stable version such as 0.5.2, without a v prefix")
    return tuple(map(int, version.split(".")))


def manifest_version(root):
    manifest = json.loads((root / "custom_components/service_route_sync/manifest.json").read_text())
    version_tuple(manifest["version"])
    return manifest["version"]


def release_notes(root, version):
    version_tuple(version)
    if manifest_version(root) != version:
        raise ValueError("Requested version must equal manifest.json version")
    changelog = (root / "CHANGELOG.md").read_text()
    sections = list(re.finditer(r"^## ([0-9]+\.[0-9]+\.[0-9]+)(?:\s.*)?$", changelog, re.M))
    versions = [section.group(1) for section in sections]
    if len(versions) != len(set(versions)):
        raise ValueError("Changelog versions must be unique")
    if not sections or sections[0].group(1) != version:
        raise ValueError("The first versioned changelog section must match the manifest")
    end = sections[1].start() if len(sections) > 1 else len(changelog)
    notes = changelog[sections[0].end() : end].strip()
    if not notes or not re.search(r"^[-*] \S", notes, re.M):
        raise ValueError("The release needs substantive changelog entries")
    return notes + "\n"


def github_api(repository, path, token, payload=None, optional=False):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    url = f"https://api.github.com/repos/{repository}" + (f"/{path}" if path else "")
    request = Request(url, data, headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if optional and error.code == 404:
            return None
        raise


def publication_checks(repository, sha, version, token, api=github_api):
    version_tuple(version)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a complete tested commit SHA")
    repo = api(repository, "", token)
    if repo["default_branch"] != "main":
        raise ValueError("This release workflow requires main as the default branch")
    head = api(repository, "git/ref/heads/main", token)["object"]["sha"]
    if head != sha:
        raise ValueError("main moved during validation; rerun the release on the new commit")
    if api(repository, f"git/ref/tags/v{version}", token, optional=True) is not None:
        raise ValueError("The release tag already exists; existing versions are never overwritten")
    if api(repository, f"releases/tags/v{version}", token, optional=True) is not None:
        raise ValueError("The GitHub release already exists; inspect it before any recovery")
    latest = api(repository, "releases/latest", token, optional=True)
    if latest and version_tuple(latest["tag_name"].removeprefix("v")) >= version_tuple(version):
        raise ValueError("The new release must be newer than the latest published version")


def verify_release(repository, sha, version, token, notes, api=github_api):
    """Read back the HACS-visible release; never repair or overwrite remote state."""
    version_tuple(version)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a complete tested commit SHA")
    tag = api(repository, f"git/ref/tags/v{version}", token)
    if tag["object"].get("type") != "commit" or tag["object"].get("sha") != sha:
        raise ValueError("The release tag does not point directly at the exact tested commit")
    release = api(repository, f"releases/tags/v{version}", token)
    if (
        release.get("tag_name") != f"v{version}"
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or not release.get("published_at")
        or (release.get("body") or "").strip() != notes.strip()
    ):
        raise ValueError("The published stable release does not match the validated release notes")
    if api(repository, "releases/latest", token)["tag_name"] != f"v{version}":
        raise ValueError("The new release is not GitHub's latest stable release")
    return release


def publish_release(repository, sha, version, token, notes, api=github_api):
    publication_checks(repository, sha, version, token, api=api)
    try:
        # Atomically create a fresh tag. Never retry writes after an uncertain response.
        api(repository, "git/refs", token, payload={"ref": f"refs/tags/v{version}", "sha": sha})
        api(
            repository,
            "releases",
            token,
            payload={
                "tag_name": f"v{version}",
                "target_commitish": sha,
                "name": f"v{version}",
                "body": notes,
                "draft": False,
                "prerelease": False,
                "make_latest": "true",
            },
        )
        return verify_release(repository, sha, version, token, notes, api=api)
    except Exception as error:
        raise RuntimeError(
            f"Publication of v{version} may be incomplete. Inspect the tag and release at "
            f"https://github.com/{repository}/releases/tag/v{version} and run --verify "
            f"from commit {sha}. Do not move/delete the tag or blindly retry --publish."
        ) from error


def write_summary(root, repository, sha, version, release):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    minimum = json.loads((root / "hacs.json").read_text())["homeassistant"]
    with Path(summary_path).open("a") as summary:
        summary.write(
            f"## Verified release v{version}\n\n"
            f"- [Published stable release]({release['html_url']})\n"
            f"- Release commit: [`{sha}`](https://github.com/{repository}/commit/{sha})\n"
            f"- Minimum Home Assistant: **{minimum}**\n"
            "- Remote tag, published notes and latest-release status verified.\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Defaults to the manifest version for local validation")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--notes", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true", help="Read-only publication checks")
    mode.add_argument("--publish", action="store_true")
    mode.add_argument(
        "--verify", action="store_true", help="Read-only check of a published release"
    )
    options = parser.parse_args()
    version = options.version or manifest_version(options.root)
    notes = release_notes(options.root, version)
    if options.notes:
        options.notes.write_text(notes)
    if not (options.preflight or options.publish or options.verify):
        print(f"Validated manifest and changelog for v{version}; no release published.")
        return
    if not options.verify and os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Publishing is permitted only from main")
    repository = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    token = os.environ["GH_TOKEN"]
    if options.preflight:
        publication_checks(repository, sha, version, token)
        print(f"Publication preflight passed for v{version} at {sha}; no writes performed.")
        return
    operation = verify_release if options.verify else publish_release
    result = operation(repository, sha, version, token, notes)
    write_summary(options.root, repository, sha, version, result)
    print(result["html_url"])


if __name__ == "__main__":
    main()
