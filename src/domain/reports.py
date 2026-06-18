"""报告创建与聚合。

将多个分片的独立报告（*.report.json）合并为一份汇总报告（summary.json）。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from core.jsonio import read_json, write_json


def merge_reports(report_dir: Path) -> Path:
    """合并报告目录下的所有分片报告。

    遍历 report_dir 下所有 *.report.json 文件，读取后按状态统计汇总，
    输出为 summary.json。

    参数:
        report_dir: 报告目录路径（如 work/reports/）

    返回:
        汇总报告文件路径（report_dir/summary.json）

    容错处理:
        如果某个报告文件 JSON 格式损坏，不会被跳过，
        而是被标记为 status="invalid_json" 并包含在汇总中。
    """
    reports = []
    for path in sorted(report_dir.glob("*.report.json")):
        report = read_json(path)
        if report is None:
            # JSON 解析失败，生成占位错误报告
            report = {
                "report": str(path),
                "status": "invalid_json",
                "error": "could not parse report",
            }
        reports.append(report)

    # 按 status 统计各报告数量
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
