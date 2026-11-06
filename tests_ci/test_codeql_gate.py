"""Ensure CodeQL findings, rather than scanner exit status alone, gate publication."""

import copy
from pathlib import Path
import tempfile
import unittest

from scripts.check_sarif import check_directory, check_report


class CodeQLGateTests(unittest.TestCase):
    def report(self, severity="0", level="warning"):
        return {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "CodeQL",
                            "rules": [
                                {
                                    "id": "test/rule",
                                    "properties": {"security-severity": severity},
                                    "defaultConfiguration": {"level": level},
                                }
                            ],
                        }
                    },
                    "results": [{"ruleId": "test/rule"}],
                }
            ],
        }

    def test_high_critical_and_error_results_block_publication(self):
        for severity, level in (("7.0", "warning"), ("9.8", "warning"), ("0", "error")):
            with self.subTest(severity=severity, level=level):
                self.assertEqual(check_report(self.report(severity, level)), ["test/rule"])

    def test_clean_and_lower_severity_results_pass(self):
        self.assertEqual(check_report(self.report("6.9")), [])
        report = self.report()
        report["runs"][0]["results"] = []
        self.assertEqual(check_report(report), [])

    def test_missing_reports_unknown_rules_and_invalid_severities_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            check_directory(Path(directory))
        report = self.report()
        report["runs"][0]["results"][0]["ruleId"] = "unknown"
        with self.assertRaises(ValueError):
            check_report(report)
        for severity in ("NaN", "inf", "-1", "11", "invalid"):
            with self.subTest(severity=severity), self.assertRaises(ValueError):
                check_report(self.report(severity))
        for report in ({}, {"version": "2.1.0", "runs": []}):
            with self.assertRaises(ValueError):
                check_report(copy.deepcopy(report))
