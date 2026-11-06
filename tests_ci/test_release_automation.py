"""Regression checks for compatibility resolution and publication boundaries."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.ha_matrix import matrix_entry, resolve_matrix
from scripts.release import publication_checks, publish_release, release_notes, version_tuple


def package(version, python=">=3.14.2"):
    return {
        "info": {"name": "homeassistant", "version": version, "requires_python": python},
        "urls": [{"yanked": False}],
    }


class MatrixTests(unittest.TestCase):
    def test_new_release_is_discovered_without_changing_the_repository_pin(self):
        def fetch(url):
            return (
                package("2026.12.0", ">=3.15.1")
                if url.endswith("homeassistant/json")
                else package("2026.9.0")
            )

        result = resolve_matrix("2026.9.0", fetch)["include"]
        self.assertEqual(result[0], {"channel": "minimum", "version": "2026.9.0", "python": "3.14"})
        self.assertEqual(result[1], {"channel": "latest", "version": "2026.12.0", "python": "3.15"})

    def test_equal_endpoints_are_tested_once_and_marked_as_both(self):
        result = resolve_matrix("2026.9.0", lambda url: package("2026.9.0"))
        self.assertEqual(len(result["include"]), 1)
        self.assertEqual(result["include"][0]["channel"], "minimum/latest")

    def test_beta_development_and_yanked_releases_are_rejected(self):
        for version in ("2026.12.0b1", "2026.12.0.dev0", "latest", "2026.12.0\nmatrix=evil"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                matrix_entry("latest", package(version))
        metadata = package("2026.11.0")
        metadata["urls"][0]["yanked"] = True
        with self.assertRaises(ValueError):
            matrix_entry("latest", metadata)

    def test_unavailable_or_substituted_minimum_cannot_silently_pass(self):
        with self.assertRaises(ValueError):
            resolve_matrix("2027.1.0", lambda url: package("2026.11.0"))
        with self.assertRaises(ValueError):
            resolve_matrix("2026.9.0", lambda url: package("2026.11.0"))

    def test_unknown_python_constraints_require_an_explicit_update(self):
        for value in (None, "", ">=3.14,<3.15", ">=4.0.0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                matrix_entry("latest", package("2026.11.0", value))


class ReleaseTests(unittest.TestCase):
    sha = "a" * 40

    def api(self, repository, path, token, payload=None, optional=False):
        self.calls.append((path, payload))
        return copy.deepcopy(self.responses[path])

    def setUp(self):
        self.calls = []
        self.responses = {
            "": {"default_branch": "main"},
            "git/ref/heads/main": {"object": {"sha": self.sha}},
            "git/ref/tags/v0.5.2": None,
            "releases/latest": {"tag_name": "v0.5.1"},
            "git/refs": {"object": {"sha": self.sha}},
            "releases": {"html_url": "https://github.com/example/repo/releases/tag/v0.5.2"},
        }

    def test_publication_creates_a_new_tag_at_the_exact_tested_commit(self):
        publish_release("example/repo", self.sha, "0.5.2", "test-token", "- Changes\n", self.api)
        self.assertEqual(self.calls[-2], ("git/refs", {"ref": "refs/tags/v0.5.2", "sha": self.sha}))
        self.assertEqual(self.calls[-1][0], "releases")
        self.assertEqual(self.calls[-1][1]["target_commitish"], self.sha)
        self.assertFalse(self.calls[-1][1]["prerelease"])

    def test_moved_main_existing_tag_or_non_increasing_version_blocks_all_writes(self):
        changes = (
            ("git/ref/heads/main", {"object": {"sha": "b" * 40}}),
            ("git/ref/tags/v0.5.2", {"object": {"sha": self.sha}}),
            ("releases/latest", {"tag_name": "v0.5.2"}),
            ("", {"default_branch": "development"}),
        )
        for path, value in changes:
            with self.subTest(path=path):
                original = self.responses[path]
                self.responses[path] = value
                self.calls = []
                with self.assertRaises(ValueError):
                    publish_release(
                        "example/repo", self.sha, "0.5.2", "test-token", "- Changes\n", self.api
                    )
                self.assertTrue(all(payload is None for _, payload in self.calls))
                self.responses[path] = original

    def test_invalid_versions_and_short_commits_are_rejected(self):
        for value in ("v0.5.2", "0.5.2b1", "0.5.2\n", "01.5.2", "$(whoami)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version_tuple(value)
        with self.assertRaises(ValueError):
            publication_checks("example/repo", "abc123", "0.5.2", "test-token", self.api)
        self.assertEqual(self.calls, [])

    def test_notes_must_match_manifest_and_first_versioned_changelog_section(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "custom_components/service_route_sync/manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"version": "0.5.2"}))
            changelog = root / "CHANGELOG.md"
            changelog.write_text(
                "# Changelog\n\n## 0.5.2 — Update\n\n- New changes.\n\n## 0.5.1\n\n- Old.\n"
            )
            self.assertEqual(release_notes(root, "0.5.2"), "- New changes.\n")
            with self.assertRaises(ValueError):
                release_notes(root, "0.5.3")
            changelog.write_text("## 0.5.3\n- Next.\n## 0.5.2\n- Current.\n")
            with self.assertRaises(ValueError):
                release_notes(root, "0.5.2")
            changelog.write_text("## 0.5.2\n\n")
            with self.assertRaises(ValueError):
                release_notes(root, "0.5.2")
