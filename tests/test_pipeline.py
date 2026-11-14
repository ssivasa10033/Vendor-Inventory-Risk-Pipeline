import numpy as np
import pandas as pd


def test_quality_gate_passes(run_result):
    c = run_result["checks"]
    assert c.loc[c["level"] == "error", "passed"].all(), c[~c["passed"]]


def test_injected_mess_is_caught(run_result):
    reasons = set(run_result["silver"]["quarantine"]["_reason"])
    assert {"duplicate_line", "unmapped_item_name"} <= reasons


def test_no_duplicate_invoice_lines_survive(run_result):
    inv = run_result["silver"]["fact_invoice_line"]
    key = ["invoice_number", "vendor_sku", "qty", "unit_price", "line_type"]
    assert not inv.duplicated(subset=key).any()


def test_all_quantities_in_base_units(run_result):
    inv = run_result["silver"]["fact_invoice_line"]
    lb = inv[inv["uom"] == "LB"]
    assert np.allclose(lb["qty_base"], lb["qty"] * 0.45359, rtol=1e-3)


def test_derived_usage_matches_hidden_truth(run_result):
    """The kitchen never records usage. We rebuild it from counts + receipts - waste.
    Check it against the simulator's hidden ground truth (usage + unlogged waste)."""
    st = run_result["storage"]
    truth = pd.read_parquet(st.raw / "_truth/daily_truth.parquet")
    truth["date"] = pd.to_datetime(truth["date"])
    weekly = run_result["gold"]["inventory_weekly"]
    w = weekly[weekly["item_id"] == "M01"].iloc[5:30]
    t = truth[truth["item_id"] == "M01"]
    true = [t.loc[(t["date"] >= r.week_start) & (t["date"] <= r.count_date), ["true_usage", "true_waste"]].sum().sum()
            for r in w.itertuples()]
    corr = np.corrcoef(w["derived_usage"], true)[0, 1]
    assert corr > 0.9, corr


def test_risk_outputs_are_sane(run_result):
    g = run_result["gold"]
    v = g["vendor_risk"]
    assert v["risk_score"].between(0, 100).all()
    assert set(v["risk_tier"]) <= {"Low", "Medium", "High"}
    assert "V08" not in set(v["vendor_id"])  # walk-in store isn't scored
    h = g["inventory_health"]
    assert (h["reorder_point"] >= h["safety_stock"]).all()
    assert set(h["abc_class"]) == {"A", "B", "C"}
