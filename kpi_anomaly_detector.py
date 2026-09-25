#!/usr/bin/env python3
"""kpi-anomaly-detector: flag anomalies in a daily KPI time series.

Reads a CSV with a date column and a numeric metric column, then detects
anomalies using a causal, explainable method:

1. Day-of-week seasonal profile — the expected value for each date is the
   median of same-weekday values in a trailing window (e.g. last 28 days),
   falling back to the window median when there are too few same-weekday
   observations. This handles the weekend-dip pattern most business KPIs have.
2. Rolling z-score — the residual (actual - expected) is compared against the
   distribution of recent residuals. A point is flagged when |z| >= threshold
   and enough history exists.

Everything is causal: a point is only ever compared against data that came
before it, so the tool behaves the way a daily scheduled check would.

Zero third-party dependencies (stdlib only).
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


@dataclass
class Config:
    window: int = 28          # trailing days used for expectation + z-score
    threshold: float = 3.0    # |z| at or above this is an anomaly
    min_history: int = 14     # minimum prior points before flagging
    min_dow_obs: int = 3      # min same-weekday obs before using the dow profile


@dataclass
class Point:
    date: date
    value: float


@dataclass
class Alert:
    date: date
    value: float
    expected: float
    residual: float
    z: float
    direction: str            # "spike" or "dip"
    window_mean: float
    window_std: float


@dataclass
class DetectionResult:
    points: list[Point]
    alerts: list[Alert]
    gaps: list[tuple[date, date]] = field(default_factory=list)  # missing date ranges
    skipped: int = 0          # points skipped for lack of history


def parse_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {raw!r}")


def load_series(path: str, date_col: str, value_col: str) -> list[Point]:
    """Load and validate the time series from CSV."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")
        if date_col not in reader.fieldnames:
            raise ValueError(f"date column {date_col!r} not found; columns: {reader.fieldnames}")
        if value_col not in reader.fieldnames:
            raise ValueError(f"value column {value_col!r} not found; columns: {reader.fieldnames}")

        points: list[Point] = []
        seen: set[date] = set()
        for lineno, row in enumerate(reader, start=2):
            d = parse_date(row[date_col])
            if d in seen:
                raise ValueError(f"duplicate date {d} (line {lineno})")
            seen.add(d)
            raw_val = (row[value_col] or "").strip()
            try:
                v = float(raw_val.replace(",", ""))
            except ValueError:
                raise ValueError(f"non-numeric value {raw_val!r} on {d} (line {lineno})")
            if math.isnan(v) or math.isinf(v):
                raise ValueError(f"invalid value {raw_val!r} on {d} (line {lineno})")
            points.append(Point(d, v))

    if not points:
        raise ValueError("no data rows found")
    points.sort(key=lambda p: p.date)
    return points


def find_gaps(points: list[Point]) -> list[tuple[date, date]]:
    """Find missing calendar days between the first and last observation."""
    gaps: list[tuple[date, date]] = []
    for prev, cur in zip(points, points[1:]):
        if cur.date > prev.date + timedelta(days=1):
            gaps.append((prev.date + timedelta(days=1), cur.date - timedelta(days=1)))
    return gaps


def detect(points: list[Point], cfg: Config) -> DetectionResult:
    """Run causal anomaly detection over the series."""
    alerts: list[Alert] = []
    # Residuals of evaluated points, in chronological order (causal: a point's
    # own residual is never part of the distribution it is scored against).
    residuals: list[float] = []
    skipped = 0

    for i, pt in enumerate(points):
        # Trailing window of prior points (causal: never looks ahead).
        start = max(0, i - cfg.window)
        prior = points[start:i]

        if len(prior) < cfg.min_history:
            skipped += 1
            continue

        # Day-of-week seasonal expectation from prior data.
        dow_vals = [p.value for p in prior if p.date.weekday() == pt.date.weekday()]
        if len(dow_vals) >= cfg.min_dow_obs:
            expected = statistics.median(dow_vals)
        else:
            expected = statistics.median(p.value for p in prior)

        residual = pt.value - expected

        # z-score of this residual against the trailing residual distribution.
        # Require enough residual history first, or early points score against
        # a near-empty distribution and produce meaningless z-scores.
        res_hist = residuals[-cfg.window:]
        residuals.append(residual)
        if len(res_hist) < cfg.min_history:
            skipped += 1
            continue

        rmean = statistics.fmean(res_hist)
        rstd = statistics.pstdev(res_hist)
        if rstd < 1e-9:
            # Flat baseline: only a non-zero residual on a perfectly flat
            # history would matter; stay conservative and do not flag.
            z = 0.0
        else:
            z = (residual - rmean) / rstd

        if abs(z) >= cfg.threshold:
            alerts.append(Alert(
                date=pt.date,
                value=pt.value,
                expected=expected,
                residual=residual,
                z=z,
                direction="spike" if residual > 0 else "dip",
                window_mean=rmean,
                window_std=rstd,
            ))

    return DetectionResult(
        points=points,
        alerts=alerts,
        gaps=find_gaps(points),
        skipped=skipped,
    )


def wow_change(points: list[Point], on: date) -> float | None:
    """Week-over-week % change for a date, or None if unavailable."""
    by_date = {p.date: p.value for p in points}
    prev = by_date.get(on - timedelta(days=7))
    cur = by_date.get(on)
    if prev is None or cur is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100.0


def write_alerts_csv(alerts: list[Alert], points: list[Point], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "value", "expected", "residual", "z_score",
                    "direction", "wow_pct_change"])
        for a in sorted(alerts, key=lambda a: abs(a.z), reverse=True):
            wow = wow_change(points, a.date)
            w.writerow([a.date.isoformat(), f"{a.value:.2f}", f"{a.expected:.2f}",
                        f"{a.residual:.2f}", f"{a.z:.2f}", a.direction,
                        f"{wow:.1f}" if wow is not None else ""])


def write_report(result: DetectionResult, cfg: Config, value_col: str, path: str) -> None:
    pts = result.points
    first, last = pts[0].date, pts[-1].date
    alerts = sorted(result.alerts, key=lambda a: abs(a.z), reverse=True)
    spikes = sum(1 for a in alerts if a.direction == "spike")
    dips = len(alerts) - spikes
    vals = [p.value for p in pts]

    lines = [
        "# KPI Anomaly Report",
        "",
        f"- **Metric:** `{value_col}`",
        f"- **Period:** {first} to {last} ({len(pts)} daily observations)",
        f"- **Method:** day-of-week seasonal profile + rolling z-score "
        f"(window={cfg.window}d, threshold=|z|>={cfg.threshold}, min history={cfg.min_history}d)",
        f"- **Points evaluated:** {len(pts) - result.skipped} "
        f"({result.skipped} skipped: insufficient history)",
        f"- **Anomalies found:** {len(alerts)} ({spikes} spikes, {dips} dips)",
        f"- **Overall median / mean:** {statistics.median(vals):.2f} / {statistics.fmean(vals):.2f}",
        "",
    ]
    if result.gaps:
        lines.append("## Data gaps (missing calendar days)")
        lines.append("")
        for gstart, gend in result.gaps:
            span = f"{gstart} to {gend}" if gstart != gend else f"{gstart}"
            lines.append(f"- {span}")
        lines.append("")

    lines += ["## Anomalies (ranked by |z-score|)", ""]
    if alerts:
        lines.append("| Date | Value | Expected | Residual | z-score | Direction | WoW % |")
        lines.append("|---|---|---|---|---|---|---|")
        for a in alerts:
            wow = wow_change(pts, a.date)
            wow_s = f"{wow:+.1f}%" if wow is not None else "n/a"
            lines.append(
                f"| {a.date} | {a.value:.2f} | {a.expected:.2f} | "
                f"{a.residual:+.2f} | {a.z:+.2f} | {a.direction} | {wow_s} |"
            )
    else:
        lines.append("No anomalies detected — the series stayed within normal bounds.")
    lines += ["", "_Generated by kpi-anomaly-detector._", ""]

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Detect anomalies in a daily KPI time series (CSV: date + metric).")
    ap.add_argument("input", help="input CSV file")
    ap.add_argument("--date-col", default="date", help="date column name (default: date)")
    ap.add_argument("--value-col", default="metric", help="metric column name (default: metric)")
    ap.add_argument("--window", type=int, default=28, help="trailing window in days (default: 28)")
    ap.add_argument("--threshold", type=float, default=3.0, help="|z| flag threshold (default: 3.0)")
    ap.add_argument("--min-history", type=int, default=14, help="min prior points before flagging (default: 14)")
    ap.add_argument("--out", default="alerts.csv", help="alerts CSV output path (default: alerts.csv)")
    ap.add_argument("--report", default="anomaly_report.md", help="markdown report path (default: anomaly_report.md)")
    args = ap.parse_args(argv)

    if args.window < 7:
        print("error: --window must be at least 7", file=sys.stderr)
        return 2
    if args.threshold <= 0:
        print("error: --threshold must be positive", file=sys.stderr)
        return 2

    try:
        points = load_series(args.input, args.date_col, args.value_col)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    cfg = Config(window=args.window, threshold=args.threshold, min_history=args.min_history)
    result = detect(points, cfg)
    write_alerts_csv(result.alerts, points, args.out)
    write_report(result, cfg, args.value_col, args.report)

    print(f"Analyzed {len(points)} points ({points[0].date} to {points[-1].date}).")
    print(f"Anomalies: {len(result.alerts)} "
          f"({sum(1 for a in result.alerts if a.direction == 'spike')} spikes, "
          f"{sum(1 for a in result.alerts if a.direction == 'dip')} dips).")
    if result.gaps:
        print(f"Gaps: {len(result.gaps)} missing-date range(s).")
    print(f"Wrote alerts -> {args.out}")
    print(f"Wrote report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
