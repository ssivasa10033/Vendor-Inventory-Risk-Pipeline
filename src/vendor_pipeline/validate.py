"""Prove the pipeline is accurate.

Because the data is simulated, the *true* answers are known: real usage, true waste, every error
deliberately injected into the exports, every line a vendor overbilled. The pipeline never sees
those (they sit in raw/_truth/). This module scores its outputs against them:

  1. Cleaning: precision / recall for each kind of injected error
  2. Money:    does cleaned spend reconcile to the true invoice total?
  3. Usage:    how close is the reconstructed weekly usage to what was really consumed?
  4. Vendors:  do measured on-time / in-full rates recover the parameters the generator used?
  5. Billing:  are the overbilled lines actually found?
  6. Stockouts: do emergency buys rank items the same way true stockouts do?
  7. Reproducibility: a fingerprint of every gold table (same seed + same code = same hash)

    vip-validate --base data --report docs/validation_report.md     # exits 1 if any gate fails
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config import VENDORS
from .simulate import ON_TIME_TREND
from .storage import LocalStorage

FINGERPRINT_EXCLUDE = {"dq_results", "validation_metrics"}  # contain run timestamps
GOLD_TABLES = ["vendor_scorecard_monthly", "vendor_risk", "billing_exceptions", "item_price_monthly",
               "inventory_weekly", "inventory_health", "kpi_summary", "dim_vendor", "dim_item", "dim_date"]


def _pr(caught: set, injected: set) -> tuple[float, float]:
    tp = len(caught & injected)
    precision = tp / len(caught) if caught else 1.0
    recall = tp / len(injected) if injected else 1.0
    return precision, recall


def fingerprint(gold: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for name in sorted(gold):
        if name in FINGERPRINT_EXCLUDE:
            continue
        df = gold[name]
        df = df.drop(columns=[c for c in df.columns if c in ("as_of", "run_at")], errors="ignore")
        df = df.reindex(sorted(df.columns), axis=1)
        out = pd.DataFrame(index=df.index)
        for c in df.columns:  # canonical text form, independent of library version / platform
            col = df[c]
            if pd.api.types.is_bool_dtype(col):
                out[c] = col.map(lambda v: "" if pd.isna(v) else str(bool(v)))
            elif pd.api.types.is_numeric_dtype(col):
                out[c] = col.astype(float).map(lambda v: "" if pd.isna(v) else f"{v:.3f}")
            elif pd.api.types.is_datetime64_any_dtype(col):
                out[c] = col.map(lambda v: "" if pd.isna(v) else v.strftime("%Y-%m-%d"))
            else:
                out[c] = col.map(lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA else str(v))
        df = out.sort_values(list(out.columns)).reset_index(drop=True)
        h.update(name.encode())
        # lineterminator pinned: to_csv defaults to os.linesep, which would split the fingerprint by OS
        h.update(df.to_csv(index=False, lineterminator="\n").encode())
    return h.hexdigest()[:16]


def run(storage: LocalStorage) -> tuple[pd.DataFrame, dict]:
    raw = Path(storage.raw)
    inj = pd.read_csv(raw / "_truth/injections.csv")
    truth_inv = pd.read_parquet(raw / "_truth/invoices_clean.parquet")
    truth_daily = pd.read_parquet(raw / "_truth/daily_truth.parquet")
    truth_daily["date"] = pd.to_datetime(truth_daily["date"])
    q = storage.read("silver", "quarantine")
    inv = storage.read("silver", "fact_invoice_line")
    dlv = storage.read("silver", "fact_delivery")
    waste = storage.read("silver", "fact_waste")
    gold = {t: storage.read("gold", t) for t in GOLD_TABLES}
    rows, detail = [], {}

    def add(area, metric, value, threshold=None, higher_is_better=True, note=""):
        passed = None if threshold is None else (value >= threshold if higher_is_better else value <= threshold)
        rows.append(dict(area=area, metric=metric, value=round(float(value), 4),
                         threshold=threshold, passed=passed, note=note))

    # ---------------------------------------------------------------- 1. cleaning
    def keys(df, a="invoice_number", b="vendor_sku"):
        return set(df[a].astype(str) + "|" + df[b].astype(str))

    qi = q[q["_source_table"] == "invoices"]
    for kind, reason in [("duplicate_line", "duplicate_line"), ("price_decimal_typo", "price_outlier"),
                         ("negative_qty_typo", "negative_qty_on_invoice")]:
        injected = set(inj.loc[inj["kind"] == kind, "key"])
        caught = keys(qi[qi["_reason"] == reason])
        p, r = _pr(caught, injected)
        add("1. Cleaning", f"{kind}: recall", r, 0.95, note=f"{len(injected)} injected")
        add("1. Cleaning", f"{kind}: precision", p, 0.90, note=f"{len(caught)} flagged")

    injected = set(inj.loc[inj["kind"] == "missing_price", "key"])
    imputed = inv[inv["price_imputed"]]
    p, r = _pr(keys(imputed), injected)
    add("1. Cleaning", "missing_price: imputed (recall)", r, 0.95, note=f"{len(injected)} injected")
    m = imputed.merge(truth_inv[["invoice_number", "vendor_sku", "unit_price"]], on=["invoice_number", "vendor_sku"],
                      suffixes=("", "_true"))
    err = (m["unit_price"] - m["unit_price_true"]).abs().max() if len(m) else 0
    add("1. Cleaning", "missing_price: max imputation error ($)", err, 0.01, higher_is_better=False)

    qc = q[q["_source_table"] == "inventory_counts"]
    count_keys = set(qc["count_date"].astype(str).str[:10] + "|" + qc["item"].astype(str))
    for kind, reason in [("blank_count", "blank_count"), ("unmapped_alias", "unmapped_item_name")]:
        injected = set(inj.loc[inj["kind"] == kind, "key"].str.replace(r" 00:00:00", "", regex=False))
        sel = qc if kind == "blank_count" else qc[qc["_reason"] == reason]  # a blank row with an unknown name is still caught
        caught = set(sel["count_date"].astype(str).str[:10] + "|" + sel["item"].astype(str))
        _, r = _pr(caught & count_keys, injected)
        add("1. Cleaning", f"{kind}: recall", r, 0.95, note=f"{len(injected)} injected")

    vendor_ok = (inv["vendor_id"] == inv["invoice_number"].str[:3]).mean()
    add("1. Cleaning", "vendor name matching accuracy", vendor_ok, 0.999,
        note=f"{len(VENDORS)} vendors from 29 spellings")

    # ---------------------------------------------------------------- 2. money
    true_total = truth_inv["line_total"].sum()
    clean_total = inv["line_amount"].sum()
    removed_legit = set(inj.loc[inj["kind"].isin(["price_decimal_typo", "negative_qty_typo"]), "key"])
    tk = truth_inv.assign(key=truth_inv["invoice_number"] + "|" + truth_inv["vendor_sku"])
    explained = tk.loc[tk["key"].isin(removed_legit), "line_total"].sum()
    gap = (true_total - explained - clean_total) / true_total
    add("2. Money", "unexplained spend gap (% of true spend)", abs(gap) * 100, 0.1, higher_is_better=False,
        note=f"true ${true_total:,.0f} | cleaned ${clean_total:,.0f} | quarantined-for-review ${explained:,.0f}")
    detail["spend"] = dict(true=true_total, cleaned=clean_total, quarantined=explained)

    # ---------------------------------------------------------------- 3. usage reconstruction
    w = gold["inventory_weekly"].copy()
    w["week_start"], w["count_date"] = pd.to_datetime(w["week_start"]), pd.to_datetime(w["count_date"])
    td = truth_daily.set_index(["item_id", "date"]).sort_index()
    waste["date"] = pd.to_datetime(waste["date"])
    true_draw = []
    for r in w.itertuples():
        t = td.loc[(r.item_id, slice(r.week_start, r.count_date)), ["true_usage", "true_waste"]].sum()
        true_draw.append((t["true_usage"], t["true_usage"] + t["true_waste"] - r.waste_logged))
    w["true_usage"], w["true_draw"] = zip(*true_draw)
    w = w.merge(gold["dim_item"][["item_id", "category"]], on="item_id")
    wmape = (w["derived_usage"] - w["true_draw"]).abs().sum() / w["true_draw"].abs().sum()
    add("3. Usage", "weekly usage error (WMAPE, all items)", wmape * 100, 10, higher_is_better=False,
        note="derived vs true stock drawdown (usage + unlogged waste)")
    corr = np.corrcoef(w["derived_usage"], w["true_draw"])[0, 1]
    add("3. Usage", "weekly usage correlation", corr, 0.95)
    shrink = (w["derived_usage"].sum() - w["true_usage"].sum()) / w["true_usage"].sum()
    add("3. Usage", "shrink captured as usage (% over true usage)", shrink * 100, note="unlogged waste shows up as usage: expected, and why waste logging matters")
    by_cat = w.groupby("category").apply(lambda g: (g["derived_usage"] - g["true_draw"]).abs().sum() / g["true_draw"].abs().sum() * 100)
    detail["usage_by_category"] = by_cat.round(2)
    detail["usage_example"] = w[w["item_id"] == "M01"][["count_date", "derived_usage", "true_draw"]].tail(26)

    # ---------------------------------------------------------------- 4. vendor parameters recovered
    d = dlv[~dlv["is_open"]].copy()
    d["received_date"] = pd.to_datetime(d["received_date"])
    start = d["received_date"].min()
    d["years"] = (d["received_date"] - start).dt.days / 365.25
    recov = []
    for vid, g in d.groupby("vendor_id"):
        v = VENDORS[vid]
        # lateness happens per delivery (every line on a truck is late together), so measure per PO
        po = g.groupby("po_number").agg(on_time=("on_time", "first"), years=("years", "first"))
        exp_on = np.clip(v.on_time_p + ON_TIME_TREND.get(vid, 0) * po["years"], 0.4, 0.99).mean()
        se_on = np.sqrt(exp_on * (1 - exp_on) / len(po))
        se_full = np.sqrt(v.fill_p * (1 - v.fill_p) / len(g))
        recov.append(dict(vendor_id=vid, vendor=v.name, deliveries=len(po), lines=len(g),
                          generator_on_time=exp_on, measured_on_time=po["on_time"].mean(),
                          on_time_z=(po["on_time"].mean() - exp_on) / se_on,
                          generator_in_full=v.fill_p, measured_in_full=g["in_full"].mean(),
                          in_full_z=(g["in_full"].mean() - v.fill_p) / se_full))
    recov = pd.DataFrame(recov)
    add("4. Vendors", "max on-time rate error vs generator", (recov["measured_on_time"] - recov["generator_on_time"]).abs().max(),
        note="absolute; see z-score gate for whether it's within sampling noise")
    add("4. Vendors", "max |z| on-time (within sampling error?)", recov["on_time_z"].abs().max(), 3.0, higher_is_better=False,
        note="|z| < 3 = the measured rate is consistent with the true parameter")
    add("4. Vendors", "max |z| in-full (within sampling error?)", recov["in_full_z"].abs().max(), 3.0, higher_is_better=False)
    detail["vendor_recovery"] = recov.round(3)

    # ---------------------------------------------------------------- 5. billing
    true_ob = set(tk.loc[tk["_overbilled"], "key"])
    b = gold["billing_exceptions"]
    found = set(b["invoice_number"].astype(str) + "|" + b["vendor_sku"].astype(str))
    p, r = _pr(found, true_ob)
    add("5. Billing", "overbilled lines found (recall)", r, 0.90, note=f"{len(true_ob)} truly overbilled")
    add("5. Billing", "overbilling flags correct (precision)", p, 0.95, note=f"{len(found)} flagged")

    # ---------------------------------------------------------------- 6. stockout signal
    h = gold["inventory_health"].set_index("item_id")
    as_of = pd.to_datetime(h["as_of"]).max()
    t12 = truth_daily[truth_daily["date"] > as_of - pd.DateOffset(months=12)]
    so_days = (t12["unmet_demand"] > 0).groupby(t12["item_id"]).sum()
    both = pd.DataFrame({"emergency": h["emergency_buys_12m"], "stockout_days": so_days}).fillna(0)
    rho = both.rank().corr().iloc[0, 1]
    add("6. Stockouts", "rank corr: emergency buys vs true stockout days", rho, 0.6,
        note="emergency walk-in buys are the only stockout evidence in the exports")

    # ---------------------------------------------------------------- 7. reproducibility
    fp = fingerprint(gold)
    add("7. Reproducibility", "gold fingerprint", 0, note=fp)
    detail["fingerprint"] = fp
    return pd.DataFrame(rows), detail


def render_report(metrics: pd.DataFrame, detail: dict) -> str:
    ok = metrics["passed"].dropna().all()
    lines = [
        "# Validation report",
        "",
        f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `vip-validate`. Gold fingerprint: `{detail['fingerprint']}`_",
        "",
        f"**Result: {'ALL GATES PASSED' if ok else 'SOME GATES FAILED'}** "
        f"({int(metrics['passed'].sum())}/{int(metrics['passed'].notna().sum())} gates)",
        "",
        "The simulator keeps the true answers hidden in `raw/_truth/`. The pipeline never reads them; this report",
        "scores its outputs against them.",
        "",
        "| Area | Metric | Value | Gate | Pass | Notes |",
        "|---|---|---:|---:|:---:|---|",
    ]
    for r in metrics.itertuples():
        lower_better = any(k in r.metric for k in ("error", "gap", "|z|"))
        gate = "" if pd.isna(r.threshold) else (f"≤ {r.threshold}" if lower_better else f"≥ {r.threshold}")
        mark = "" if r.passed is None or pd.isna(r.passed) else ("✅" if r.passed else "❌")
        val = r.note if r.metric == "gold fingerprint" else f"{r.value:g}"
        note = "" if r.metric == "gold fingerprint" else r.note
        esc = lambda x: str(x).replace("|", "\\|")  # noqa: E731  keep pipes from breaking the table
        lines.append(f"| {r.area} | {esc(r.metric)} | {esc(val)} | {gate} | {mark} | {esc(note)} |")
    lines += ["", "## Vendor parameters recovered from the data", "",
              "The generator's settings are the ground truth for each supplier's reliability. The pipeline only sees deliveries.", "",
              detail["vendor_recovery"].to_markdown(index=False), "",
              "## Usage reconstruction error by category (WMAPE %)", "",
              detail["usage_by_category"].to_frame("wmape_pct").to_markdown(), "",
              "## Example: chicken breast, last 26 weeks (kg)", "",
              detail["usage_example"].assign(count_date=lambda x: x["count_date"].dt.date).round(1).to_markdown(index=False), ""]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="data")
    p.add_argument("--report", default=None, help="write markdown report here (default: <base>/validation_report.md)")
    a = p.parse_args(argv)
    st = LocalStorage(a.base)
    metrics, detail = run(st)
    st.write("gold", "validation_metrics", metrics)
    report = Path(a.report or Path(a.base) / "validation_report.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(metrics, detail), encoding="utf-8")
    print(metrics.to_string(index=False))
    print(f"\nfingerprint {detail['fingerprint']}  ->  report: {report}")
    failed = metrics[metrics["passed"] == False]  # noqa: E712
    if len(failed):
        print(f"\n{len(failed)} validation gate(s) FAILED", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
