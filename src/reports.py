"""Report creation and aggregation."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from jsonio import read_json, write_json


def merge_reports(report_dir: Path) -> Path:
    reports = []
    for path in sorted(report_dir.glob("*.report.json")):
        report = read_json(path)
        if report is None:
            report = {
                "report": str(path),
                "status": "invalid_json",
                "error": "could not parse report",
            }
        reports.append(report)

    summary = {
        "total_reports": len(reports),
        "status_counts": dict(
            Counter(item.get("status", "unknown") for item in reports)
        ),
        "reports": reports,
    }
    output = report_dir / "summary.json"
    write_json(output, summary)
    return output
