"""Ensure workflow changes cannot accidentally broaden CI access or bypass release checks."""

import copy
from pathlib import Path
import unittest

from scripts.check_workflows import check_workflow


class WorkflowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.workflow = {
            "permissions": {"contents": "read"},
            "on": {"pull_request": ""},
            "jobs": {
                "test": {
                    "timeout-minutes": "10",
                    "steps": [
                        {
                            "uses": "actions/checkout@" + "a" * 40,
                            "with": {"persist-credentials": "false"},
                        }
                    ],
                }
            },
        }

    def test_read_only_pinned_checkout_is_accepted(self):
        self.assertEqual(check_workflow(Path("tests.yml"), self.workflow), [])

    def test_credential_retention_floating_actions_and_write_tokens_are_rejected(self):
        for change in ("credentials", "pin", "permissions", "trigger", "continue", "secrets"):
            with self.subTest(change=change):
                data = copy.deepcopy(self.workflow)
                job = data["jobs"]["test"]
                if change == "credentials":
                    job["steps"][0]["with"] = {}
                elif change == "pin":
                    job["steps"][0]["uses"] = "actions/checkout@v7"
                elif change == "permissions":
                    job["permissions"] = {"contents": "write"}
                elif change == "trigger":
                    data["on"] = {"pull_request_target": ""}
                elif change == "continue":
                    job["continue-on-error"] = "true"
                else:
                    job["secrets"] = "inherit"
                self.assertTrue(check_workflow(Path("tests.yml"), data))

    def test_release_cannot_drop_a_required_gate(self):
        data = {
            "permissions": {"contents": "read"},
            "on": {"workflow_dispatch": ""},
            "jobs": {
                "tests": {"uses": "./.github/workflows/tests.yml"},
                "security": {"uses": "./.github/workflows/security.yml"},
                "publish": {
                    "timeout-minutes": "5",
                    "needs": ["prepare", "tests", "security"],
                    "if": "${{ inputs.publish && github.ref == 'refs/heads/main' }}",
                },
            },
        }
        self.assertEqual(check_workflow(Path("release.yml"), data), [])
        data["jobs"]["publish"]["needs"].remove("security")
        self.assertTrue(check_workflow(Path("release.yml"), data))

    def test_release_cannot_publish_after_failed_gates_or_use_substitute_checks(self):
        import yaml

        workflow = yaml.load(
            Path(".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader
        )
        for change in ("always", "no_opt_in", "no_branch", "tests", "security"):
            with self.subTest(change=change):
                data = copy.deepcopy(workflow)
                if change == "always":
                    data["jobs"]["publish"]["if"] = (
                        "${{ always() && inputs.publish && github.ref == 'refs/heads/main' }}"
                    )
                elif change == "no_opt_in":
                    data["jobs"]["publish"]["if"] = "${{ github.ref == 'refs/heads/main' }}"
                elif change == "no_branch":
                    data["jobs"]["publish"]["if"] = "${{ inputs.publish }}"
                else:
                    data["jobs"][change] = {"timeout-minutes": "5", "steps": [{"run": "true"}]}
                self.assertTrue(check_workflow(Path("release.yml"), data))

    def test_alternative_trigger_syntax_cannot_enable_privileged_events(self):
        for triggers in ("pull_request_target", ["push", "workflow_run"]):
            with self.subTest(triggers=triggers):
                data = copy.deepcopy(self.workflow)
                data["on"] = triggers
                self.assertTrue(check_workflow(Path("tests.yaml"), data))
