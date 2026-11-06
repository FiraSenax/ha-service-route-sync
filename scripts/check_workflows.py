"""Enforce the repository's credential and workflow permission boundaries."""

from pathlib import Path
import re

import yaml


def check_workflow(path, document):
    errors = []
    if document.get("permissions") != {"contents": "read"}:
        errors.append("Default token permissions must be contents: read")
    triggers = document.get("on", {})
    trigger_names = {triggers} if isinstance(triggers, str) else set(triggers)
    if {"pull_request_target", "workflow_run"} & trigger_names:
        errors.append("Privileged untrusted-code triggers are forbidden")
    for name, job in document.get("jobs", {}).items():
        permissions = job.get("permissions", {})
        for scope, value in permissions.items():
            if value != "write":
                continue
            allowed = (
                (path.name == "release.yml" and name == "publish" and scope == "contents")
                or (
                    path.name == "release.yml" and name == "security" and scope == "security-events"
                )
                or (path.name == "security.yml" and name == "codeql" and scope == "security-events")
            )
            if not allowed:
                errors.append(f"{name}: unexpected write permission for {scope}")
        if job.get("continue-on-error") not in (None, "false"):
            errors.append(f"{name}: required jobs must fail closed")
        if job.get("secrets"):
            errors.append(f"{name}: CI must not inherit repository secrets")
        if "uses" not in job and not re.fullmatch(r"[1-9][0-9]?", job.get("timeout-minutes", "")):
            errors.append(f"{name}: explicit job timeout required")
        for item in [job, *job.get("steps", [])]:
            uses = item.get("uses")
            if uses and not (
                re.fullmatch(r"\./\.github/workflows/[A-Za-z0-9_-]+\.yml", uses)
                or re.fullmatch(r"[A-Za-z0-9_./-]+@[0-9a-f]{40}", uses)
            ):
                errors.append(f"{name}: external Actions must be pinned to a complete commit SHA")
            if uses and uses.startswith("actions/checkout@"):
                if item.get("with", {}).get("persist-credentials") != "false":
                    errors.append(f"{name}: checkout must not retain credentials")
            if item.get("continue-on-error") not in (None, "false"):
                errors.append(f"{name}: required steps must fail closed")
    if path.name == "release.yml":
        publish = document.get("jobs", {}).get("publish", {})
        if not {"prepare", "tests", "security"} <= set(publish.get("needs", [])):
            errors.append("Release publication must require metadata, tests and security")
        if set(triggers) != {"workflow_dispatch"}:
            errors.append("Release publication must be explicitly dispatched")
    return errors


def main():
    errors = []
    for path in sorted(Path(".github/workflows").iterdir()):
        if path.suffix not in {".yaml", ".yml"}:
            continue
        document = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        errors.extend(f"{path}: {message}" for message in check_workflow(path, document))
    if errors:
        raise SystemExit("\n".join(errors))
    print("Workflow token, action-pin and release-gate policy passed.")


if __name__ == "__main__":
    main()
