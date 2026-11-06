"""Fail the release security job for high/critical CodeQL findings or errors."""

import argparse
import json
import math
from pathlib import Path


def check_report(report):
    if report.get("version") != "2.1.0" or not report.get("runs"):
        raise ValueError("Expected a SARIF 2.1.0 report with analysis runs")
    findings = []
    for run in report["runs"]:
        driver = run["tool"]["driver"]
        if driver["name"] != "CodeQL":
            raise ValueError("Expected CodeQL analysis results")
        rules = {rule["id"]: rule for rule in driver.get("rules", [])}
        for result in run["results"]:
            rule_id = result["ruleId"]
            if rule_id not in rules:
                raise ValueError(f"Result references an unknown CodeQL rule: {rule_id}")
            rule = rules[rule_id]
            level = result.get(
                "level", rule.get("defaultConfiguration", {}).get("level", "warning")
            )
            severity = float(rule.get("properties", {}).get("security-severity", "0"))
            if not math.isfinite(severity) or not 0 <= severity <= 10:
                raise ValueError(f"Invalid security severity for {rule_id}")
            if severity >= 7 or level == "error":
                findings.append(rule_id)
    return findings


def check_directory(directory):
    reports = sorted(directory.glob("*.sarif"))
    if not reports:
        raise ValueError("No CodeQL SARIF results were produced")
    findings = []
    for path in reports:
        findings.extend(check_report(json.loads(path.read_text())))
    if findings:
        raise ValueError("Blocking CodeQL findings: " + ", ".join(sorted(set(findings))))
    return len(reports)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    options = parser.parse_args()
    count = check_directory(options.directory)
    print(f"Checked {count} CodeQL reports: no high/critical findings or errors.")


if __name__ == "__main__":
    main()
