# kpi-anomaly-detector

A zero-dependency CLI that flags anomalies in a daily KPI time series — revenue, active users, pipeline row counts, ad spend, whatever you track.

## The problem

BI teams watch dozens of metrics, and the usual "alert me if it moved X%" approach fails on metrics with weekly seasonality: a Sunday dip in B2B revenue is normal, a Tuesday dip is not. Thresholds either fire constantly or miss the real incidents.

## The approach

Causal, explainable detection — no ML black box, no training step:

1. **Day-of-week seasonal profile.** The expected value for each date is the median of same-weekday values in a trailing window (default 28 days), falling back to the window median when there are too few same-weekday observations. Handles the weekend-dip pattern most business KPIs have.
2. **Rolling z-score on residuals.** Each point's residual (actual − expected) is scored against the distribution of recent residuals. Flagged when |z| ≥ threshold (default 3.0) with enough history.

Everything is **causal**: a point is only ever compared against data that came before it, so the tool behaves exactly like a daily scheduled check would. Points without enough history are skipped rather than scored against a meaningless baseline.

## Quickstart

```bash
# generate the deterministic sample dataset (180 days, 4 injected anomalies)
python3 generate_sample.py

# run detection
python3 kpi_anomaly_detector.py sample_data/kpi_daily.csv \
  --value-col revenue \
  --out alerts.csv \
  --report anomaly_report.md
```

Output:

```
Analyzed 180 points (2026-03-29 to 2026-09-24).
Anomalies: 4 (2 spikes, 2 dips).
Wrote alerts -> alerts.csv
Wrote report -> anomaly_report.md
```

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--date-col` | `date` | Date column name (accepts `YYYY-MM-DD`, `YYYY/MM/DD`, `DD-MM-YYYY`, `MM/DD/YYYY`) |
| `--value-col` | `metric` | Metric column name |
| `--window` | `28` | Trailing window in days for expectation + z-score |
| `--threshold` | `3.0` | Flag when \|z-score\| ≥ this |
| `--min-history` | `14` | Minimum prior points before a point can be flagged |
| `--out` | `alerts.csv` | Alerts CSV: date, value, expected, residual, z-score, direction, week-over-week % change |
| `--report` | `anomaly_report.md` | Markdown report with summary stats, data-gap listing, and ranked anomaly table |

The report also surfaces **missing calendar days** (data gaps), which are usually the first sign of a broken upstream feed.

## Example output

From the bundled sample data (4 anomalies injected at fixed dates with a fixed seed):

| Date | Value | Expected | Residual | z-score | Direction | WoW % |
|---|---|---|---|---|---|---|
| 2026-08-06 | 28539.88 | 11415.50 | +17124.38 | +38.82 | spike | +153.2% |
| 2026-05-13 | 20566.36 | 10757.38 | +9808.99 | +15.10 | spike | +88.2% |
| 2026-06-27 | 2984.30 | 7960.51 | −4976.20 | −12.82 | dip | −61.5% |
| 2026-09-05 | 4691.50 | 8767.08 | −4075.58 | −8.58 | dip | −48.0% |

All 4 injected anomalies detected, zero false positives.

## Testing

```bash
python3 test_detector.py   # 22 checks: end-to-end CLI, injected anomalies,
                           # clean flat series (no false positives), bad-input handling
```

## Why this, technically

- **Median, not mean**, for the seasonal profile — one past promo spike shouldn't move the baseline.
- **Residuals, not raw values**, for the z-score — detrends the series implicitly and keeps the statistic meaningful under growth.
- **Population stddev** on residuals, trailing window only — bounded memory, constant-time-ish per point, trivially schedulable in Airflow/dbt as a post-load check.
- Stdlib only (`csv`, `statistics`, `datetime`, `argparse`) — runs anywhere Python 3.10+ exists, no dependency to pin or audit.

## Limits (honest ones)

- Daily grain only; intraday seasonality needs a smaller grain and a different profile key.
- Abrupt level shifts (e.g., a business doubles overnight) will read as anomalies until the window absorbs the new level — by design, since that *is* worth a human look.
- Not a substitute for proper forecasting; it's a guardrail, not a model.

## License

MIT — see [LICENSE](LICENSE).
