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


def release_notes(root, version):
    version_tuple(version)
    manifest = json.loads((root / "custom_components/service_route_sync/manifest.json").read_text())
    if manifest["version"] != version:
        raise ValueError("Requested version must equal manifest.json version")
    changelog = (root / "CHANGELOG.md").read_text()
    sections = list(re.finditer(r"^## ([0-9]+\.[0-9]+\.[0-9]+)(?:\s.*)?$", changelog, re.M))
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
    request = Request(f"https://api.github.com/repos/{repository}/{path}", data, headers)
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
    latest = api(repository, "releases/latest", token, optional=True)
    if latest and version_tuple(latest["tag_name"].removeprefix("v")) >= version_tuple(version):
        raise ValueError("The new release must be newer than the latest published version")


def publish_release(repository, sha, version, token, notes, api=github_api):
    publication_checks(repository, sha, version, token, api=api)
    # Atomically create a fresh tag at the tested SHA. Do not reuse an existing tag.
    api(repository, "git/refs", token, payload={"ref": f"refs/tags/v{version}", "sha": sha})
    return api(
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--publish", action="store_true")
    options = parser.parse_args()
    notes = release_notes(options.root, options.version)
    if options.notes:
        options.notes.write_text(notes)
    if not options.publish:
        print(f"Validated manifest and changelog for v{options.version}; no release published.")
        return
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Publishing is permitted only from main")
    repository = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    token = os.environ["GH_TOKEN"]
    result = publish_release(repository, sha, options.version, token, notes)
    print(result["html_url"])


if __name__ == "__main__":
    main()
