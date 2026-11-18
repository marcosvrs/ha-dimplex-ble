"""Fail unless every integration function is executed by the test suite."""

import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    """Check coverage.py's per-function execution data."""
    report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("coverage.json")
    try:
        report: dict[str, Any] = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(f"Unable to read coverage report {report_path}: {error}", file=sys.stderr)
        return 1

    uncovered: list[str] = []
    total = 0
    for path, file_report in report.get("files", {}).items():
        for name, function_report in file_report.get("functions", {}).items():
            if name == "(no function)":
                continue
            total += 1
            if not function_report.get("executed_lines"):
                uncovered.append(f"{path}:{name}")

    if not total:
        print("No functions found in coverage report", file=sys.stderr)
        return 1
    if uncovered:
        print("Uncovered functions:", file=sys.stderr)
        print("\n".join(uncovered), file=sys.stderr)
        return 1

    print(f"Function coverage: {total}/{total} (100%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
