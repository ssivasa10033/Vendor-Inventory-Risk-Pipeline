"""Data quality gates. "error" checks fail the run (and the CI build); "warn" checks just get logged."""

from __future__ import annotations

import pandas as pd


def run_checks(bronze: dict, silver: dict, gold: dict) -> pd.DataFrame:
    res = []

    def check(name, passed, level="error", detail=""):
        res.append(dict(check=name, passed=bool(passed), level=level, detail=str(detail)))

    q = silver["quarantine"]
    for src, raw_name in [("invoices", "invoices"), ("inventory_counts", "inventory_counts"), ("waste_log", "waste_log")]:
        rate = (q["_source_table"] == src).sum() / max(len(bronze[raw_name]), 1)
        check(f"quarantine_rate_{src} < 5%", rate < 0.05, "error", f"{rate:.2%}")
        check(f"quarantine_rate_{src} < 2%", rate < 0.02, "warn", f"{rate:.2%}")

    for t, keys in {"fact_invoice_line": ["invoice_number", "vendor_id", "item_id", "invoice_date"],
                    "fact_delivery": ["po_number", "vendor_id", "item_id", "order_date"],
                    "fact_inventory_count": ["count_date", "item_id"]}.items():
        nulls = int(silver[t][keys].isna().sum().sum())
        check(f"{t}: no null keys", nulls == 0, "error", f"{nulls} nulls")
        check(f"{t}: not empty", len(silver[t]) > 0, "error", f"{len(silver[t])} rows")

    inv = silver["fact_invoice_line"]
    bad = int(((inv["line_type"] == "INV") & (inv["qty_base"] <= 0)).sum())
    check("invoice lines have positive base qty", bad == 0, "error", f"{bad} rows")

    w = gold["inventory_weekly"]
    neg = w["negative_usage_flag"].mean() if len(w) else 1
    check("derived usage negative < 10% of weeks", neg < 0.10, "error", f"{neg:.2%}")

    h = gold["inventory_health"]
    check("every item has a reorder point", h["reorder_point"].notna().all(), "warn",
          ", ".join(h.loc[h["reorder_point"].isna(), "item_id"]))
    v = gold["vendor_risk"]
    check("risk scores within 0-100", v["risk_score"].between(0, 100).all(), "error")
    return pd.DataFrame(res)
