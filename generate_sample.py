#!/usr/bin/env python3
"""Generate a deterministic sample KPI time series with known injected anomalies.

Writes sample_data/kpi_daily.csv: 180 days of a daily revenue-like metric with
weekly seasonality (weekend dip), a mild upward trend, noise, and 4 injected
anomalies (2 spikes, 2 dips) whose dates are printed for test verification.
"""

import csv
import math
import os
import random
from datetime import date, timedelta

SEED = 42
START = date(2026, 3, 29)   # a Sunday, so day 1..180 covers full weeks
DAYS = 180

# (day_offset_from_start, multiplier): injected anomalies. Dates are printed.
INJECTED = {
    45: 1.9,    # spike
    90: 0.35,   # dip
    130: 2.4,   # spike
    160: 0.5,   # dip
}


def main() -> None:
    rng = random.Random(SEED)
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "kpi_daily.csv")

    rows = []
    for i in range(DAYS):
        d = START + timedelta(days=i)
        base = 10000.0
        seasonal = 0.72 if d.weekday() >= 5 else 1.0      # weekend dip
        trend = 1.0 + 0.0015 * i                          # slow growth
        noise = 1.0 + rng.gauss(0, 0.04)
        value = base * seasonal * trend * noise
        if i in INJECTED:
            value *= INJECTED[i]
        rows.append((d.isoformat(), round(value, 2)))

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "revenue"])
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows -> {out_path}")
    print("Injected anomaly dates:")
    for offset, mult in sorted(INJECTED.items()):
        kind = "spike" if mult > 1 else "dip"
        print(f"  {(START + timedelta(days=offset)).isoformat()} ({kind}, x{mult})")


if __name__ == "__main__":
    main()
