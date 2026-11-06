"""Resolve the declared minimum and latest stable HA release for GitHub Actions."""

import argparse
import json
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen

STABLE = re.compile(r"[0-9]{4}\.[0-9]+\.[0-9]+")


def stable_version(value):
    if not isinstance(value, str) or not STABLE.fullmatch(value):
        raise ValueError(f"Expected a stable HA version, got {value!r}")
    return tuple(int(part) for part in value.split("."))


def fetch_json(url):
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "service-route-sync-ci"})
            with urlopen(request, timeout=30) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("Package metadata exceeds the size limit")
            return json.loads(data)
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def matrix_entry(channel, metadata, expected=None):
    info = metadata["info"]
    version = info["version"]
    stable_version(version)
    if expected is not None and version != expected:
        raise ValueError("PyPI returned a different Home Assistant version")
    if info["name"].lower() != "homeassistant":
        raise ValueError("Unexpected PyPI project")
    if not any(not item.get("yanked", False) for item in metadata["urls"]):
        raise ValueError(f"Home Assistant {version} has no non-yanked distributions")
    # Fail closed if HA changes its Python constraint syntax; never guess a runtime.
    requirement = info["requires_python"]
    match = re.fullmatch(r">=(3\.[0-9]+)(?:\.[0-9]+)?", requirement or "")
    if match is None:
        raise ValueError(f"Unsupported HA Python constraint: {requirement!r}")
    return {"channel": channel, "version": version, "python": match[1]}


def resolve_matrix(minimum, fetch=fetch_json):
    minimum_tuple = stable_version(minimum)
    latest = matrix_entry("latest", fetch("https://pypi.org/pypi/homeassistant/json"))
    if minimum_tuple > stable_version(latest["version"]):
        raise ValueError("Declared HA minimum is newer than the latest stable release")
    floor = matrix_entry(
        "minimum",
        fetch(f"https://pypi.org/pypi/homeassistant/{minimum}/json"),
        expected=minimum,
    )
    if floor["version"] == latest["version"]:
        floor["channel"] = "minimum/latest"
        return {"include": [floor]}
    return {"include": [floor, latest]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hacs", type=Path, default=Path("hacs.json"))
    parser.add_argument("--github-output", type=Path)
    options = parser.parse_args()
    minimum = json.loads(options.hacs.read_text())["homeassistant"]
    result = json.dumps(resolve_matrix(minimum), separators=(",", ":"))
    print(result)
    if options.github_output:
        with options.github_output.open("a") as output:
            output.write(f"matrix={result}\n")


if __name__ == "__main__":
    main()
