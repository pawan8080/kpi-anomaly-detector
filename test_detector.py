#!/usr/bin/env python3
"""Smoke tests for kpi-anomaly-detector.

1. Generate the deterministic sample series (4 known injected anomalies).
2. Run the CLI end to end via subprocess; assert exit code 0 and outputs exist.
3. Assert all 4 injected anomaly dates are flagged, with correct directions.
4. Assert a flat, clean series produces zero anomalies.
5. Assert bad inputs fail gracefully (missing file, bad date, non-numeric value).

Run: python3 test_detector.py
"""

import csv
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "kpi_anomaly_detector.py")
GEN = os.path.join(HERE, "generate_sample.py")

PASS = 0


def check(name: str, cond: bool) -> None:
    global PASS
    assert cond, f"FAILED: {name}"
    PASS += 1
    print(f"  ok - {name}")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, CLI, *args],
        capture_output=True, text=True, cwd=HERE,
    )


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="kpi_anomaly_test_")

    print("== 1. sample data generation ==")
    r = subprocess.run([sys.executable, GEN], capture_output=True, text=True, cwd=HERE)
    check("generator exits 0", r.returncode == 0)
    sample = os.path.join(HERE, "sample_data", "kpi_daily.csv")
    check("sample csv exists", os.path.exists(sample))
    with open(sample, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    check("180 data rows", len(rows) == 180)

    # Injected anomalies (must match generate_sample.py INJECTED).
    expected = {
        "2026-05-13": "spike",
        "2026-06-27": "dip",
        "2026-08-06": "spike",
        "2026-09-05": "dip",
    }

    print("== 2. end-to-end CLI run ==")
    alerts_csv = os.path.join(tmp, "alerts.csv")
    report_md = os.path.join(tmp, "report.md")
    r = run_cli(sample, "--value-col", "revenue",
                "--out", alerts_csv, "--report", report_md)
    check("cli exits 0", r.returncode == 0)
    check("alerts.csv written", os.path.exists(alerts_csv))
    check("report.md written", os.path.exists(report_md))
    print("   cli stdout:", r.stdout.strip().splitlines()[0])

    print("== 3. injected anomalies detected ==")
    with open(alerts_csv, encoding="utf-8") as fh:
        alerts = {row["date"]: row for row in csv.DictReader(fh)}
    for d, direction in expected.items():
        check(f"{d} flagged", d in alerts)
        check(f"{d} direction={direction}", alerts[d]["direction"] == direction)
    with open(report_md, encoding="utf-8") as fh:
        report = fh.read()
    check("report names the metric", "revenue" in report)
    check("report lists anomaly dates",
          all(d in report for d in expected))

    print("== 4. clean flat series -> no anomalies ==")
    flat = os.path.join(tmp, "flat.csv")
    with open(flat, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "metric"])
        from datetime import date as d, timedelta
        base = d(2026, 1, 1)
        for i in range(60):
            w.writerow([(base + timedelta(days=i)).isoformat(), 100.0])
    r = run_cli(flat, "--out", os.path.join(tmp, "flat_alerts.csv"),
                "--report", os.path.join(tmp, "flat_report.md"))
    check("flat series exits 0", r.returncode == 0)
    with open(os.path.join(tmp, "flat_alerts.csv"), encoding="utf-8") as fh:
        check("flat series: zero alerts", len(list(csv.DictReader(fh))) == 0)

    print("== 5. bad inputs fail gracefully ==")
    r = run_cli(os.path.join(tmp, "nope.csv"))
    check("missing file exits nonzero", r.returncode != 0)
    bad = os.path.join(tmp, "bad.csv")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("date,metric\nnot-a-date,5\n")
    r = run_cli(bad)
    check("bad date exits nonzero", r.returncode != 0)
    bad2 = os.path.join(tmp, "bad2.csv")
    with open(bad2, "w", encoding="utf-8") as fh:
        fh.write("date,metric\n2026-01-01,abc\n")
    r = run_cli(bad2)
    check("non-numeric value exits nonzero", r.returncode != 0)
    r = run_cli(sample, "--window", "3")
    check("--window < 7 rejected", r.returncode != 0)

    print(f"\nAll {PASS} checks passed.")


if __name__ == "__main__":
    main()
