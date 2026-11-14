# Validation report

_Generated 2026-11-14 22:10 UTC by `vip-validate`. Gold fingerprint: `8461725598dd5522`_

**Result: ALL GATES PASSED** (19/19 gates)

The simulator keeps the true answers hidden in `raw/_truth/`. The pipeline never reads them; this report
scores its outputs against them.

| Area | Metric | Value | Gate | Pass | Notes |
|---|---|---:|---:|:---:|---|
| 1. Cleaning | duplicate_line: recall | 1 | ≥ 0.95 | ✅ | 134 injected |
| 1. Cleaning | duplicate_line: precision | 1 | ≥ 0.9 | ✅ | 134 flagged |
| 1. Cleaning | price_decimal_typo: recall | 1 | ≥ 0.95 | ✅ | 17 injected |
| 1. Cleaning | price_decimal_typo: precision | 1 | ≥ 0.9 | ✅ | 17 flagged |
| 1. Cleaning | negative_qty_typo: recall | 1 | ≥ 0.95 | ✅ | 16 injected |
| 1. Cleaning | negative_qty_typo: precision | 1 | ≥ 0.9 | ✅ | 16 flagged |
| 1. Cleaning | missing_price: imputed (recall) | 1 | ≥ 0.95 | ✅ | 57 injected |
| 1. Cleaning | missing_price: max imputation error ($) | 0.005 | ≤ 0.01 | ✅ |  |
| 1. Cleaning | blank_count: recall | 1 | ≥ 0.95 | ✅ | 67 injected |
| 1. Cleaning | unmapped_alias: recall | 1 | ≥ 0.95 | ✅ | 100 injected |
| 1. Cleaning | vendor name matching accuracy | 1 | ≥ 0.999 | ✅ | 8 vendors from 29 spellings |
| 2. Money | unexplained spend gap (% of true spend) | 0.0002 | ≤ 0.1 | ✅ | true $883,485 \| cleaned $880,165 \| quarantined-for-review $3,321 |
| 3. Usage | weekly usage error (WMAPE, all items) | 5.4318 | ≤ 10.0 | ✅ | derived vs true stock drawdown (usage + unlogged waste) |
| 3. Usage | weekly usage correlation | 0.996 | ≥ 0.95 | ✅ |  |
| 3. Usage | shrink captured as usage (% over true usage) | 2.1683 |  |  | unlogged waste shows up as usage: expected, and why waste logging matters |
| 4. Vendors | max on-time rate error vs generator | 0.0313 |  |  | absolute; see z-score gate for whether it's within sampling noise |
| 4. Vendors | max \|z\| on-time (within sampling error?) | 1.9651 | ≤ 3.0 | ✅ | \|z\| < 3 = the measured rate is consistent with the true parameter |
| 4. Vendors | max \|z\| in-full (within sampling error?) | 2.2986 | ≤ 3.0 | ✅ |  |
| 5. Billing | overbilled lines found (recall) | 1 | ≥ 0.9 | ✅ | 40 truly overbilled |
| 5. Billing | overbilling flags correct (precision) | 1 | ≥ 0.95 | ✅ | 40 flagged |
| 6. Stockouts | rank corr: emergency buys vs true stockout days | 0.8352 | ≥ 0.6 | ✅ | emergency walk-in buys are the only stockout evidence in the exports |
| 7. Reproducibility | gold fingerprint | 8461725598dd5522 |  |  |  |

## Vendor parameters recovered from the data

The generator's settings are the ground truth for each supplier's reliability. The pipeline only sees deliveries.

| vendor_id   | vendor                 |   deliveries |   lines |   generator_on_time |   measured_on_time |   on_time_z |   generator_in_full |   measured_in_full |   in_full_z |
|:------------|:-----------------------|-------------:|--------:|--------------------:|-------------------:|------------:|--------------------:|-------------------:|------------:|
| V01         | Northfield Foodservice |          261 |    1138 |               0.93  |              0.939 |       0.551 |                0.95 |              0.965 |       2.299 |
| V02         | Peel Fresh Produce     |          276 |    2413 |               0.9   |              0.895 |      -0.275 |                0.9  |              0.897 |      -0.523 |
| V03         | Lakeshore Meats        |          417 |    2484 |               0.88  |              0.911 |       1.965 |                0.92 |              0.908 |      -2.165 |
| V04         | Credit Valley Dairy    |          277 |    1245 |               0.96  |              0.96  |       0.025 |                0.97 |              0.968 |      -0.44  |
| V05         | Spice Route Imports    |          131 |     402 |               0.603 |              0.58  |      -0.542 |                0.85 |              0.846 |      -0.237 |
| V06         | GTA Packaging Supply   |          138 |     407 |               0.9   |              0.891 |      -0.341 |                0.94 |              0.939 |      -0.121 |
| V07         | Halton Bakery Co       |          643 |    1549 |               0.97  |              0.975 |       0.761 |                0.98 |              0.978 |      -0.548 |

## Usage reconstruction error by category (WMAPE %)

| category   |   wmape_pct |
|:-----------|------------:|
| Bakery     |        2.29 |
| Dairy      |        4.25 |
| Dry Goods  |        4.95 |
| Packaging  |        9.1  |
| Produce    |        3.82 |
| Protein    |        1.77 |
| Spices     |        6.71 |

## Example: chicken breast, last 26 weeks (kg)

| count_date   |   derived_usage |   true_draw |
|:-------------|----------------:|------------:|
| 2025-03-08   |            61   |        61   |
| 2025-03-15   |            43.3 |        42.9 |
| 2025-03-22   |            44.4 |        42.8 |
| 2025-03-29   |            73.3 |        74.7 |
| 2025-04-05   |            61.5 |        60.9 |
| 2025-04-12   |            64.5 |        65.9 |
| 2025-04-19   |            67.5 |        66   |
| 2025-04-26   |            63.6 |        63.8 |
| 2025-05-03   |            63.7 |        63.4 |
| 2025-05-10   |            61.5 |        62.6 |
| 2025-05-17   |            63.1 |        62.8 |
| 2025-05-24   |            70.7 |        68.8 |
| 2025-05-31   |            61.7 |        63.6 |
| 2025-06-07   |            75.9 |        74.4 |
| 2025-06-14   |            82.9 |        82.2 |
| 2025-06-21   |            83.2 |        82.9 |
| 2025-06-28   |            69   |        71.6 |
| 2025-07-05   |            34.6 |        33.9 |
| 2025-07-12   |            32.3 |        32.5 |
| 2025-07-19   |            33.6 |        32.7 |
| 2025-07-26   |            28.9 |        30   |
| 2025-08-02   |            30.8 |        29.9 |
| 2025-08-09   |            34.5 |        35.1 |
| 2025-08-16   |            26.6 |        26.8 |
| 2025-08-23   |            33.4 |        34.2 |
| 2025-08-30   |            36.1 |        33.6 |
