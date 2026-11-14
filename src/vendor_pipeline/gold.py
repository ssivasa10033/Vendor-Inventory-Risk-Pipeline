"""Gold = business-ready metrics. One table per question someone will actually ask.

  gold_vendor_scorecard_monthly  "How did each vendor perform each month?"
  gold_vendor_risk               "Which vendors are a risk to us right now, and why?"
  gold_billing_exceptions        "Where were we billed for product we never got?"
  gold_item_price_monthly        "What are we paying vs the market, and how fast is it rising?"
  gold_inventory_weekly          "What did we actually use each week?" (reconstructed)
  gold_inventory_health          "What should we reorder, and at what level?"
  gold_kpi_summary               headline numbers for the dashboard cards
  dim_vendor / dim_item / dim_date  star-schema dimensions for Power BI
"""

from __future__ import annotations

import numpy as np
import pandas as pd

Z_95 = 1.65
BACKUP_VENDOR = "V08"


def _month(s: pd.Series) -> pd.Series:
    return s.dt.to_period("M").astype(str)


def _clip01(x):
    return np.clip(x, 0, 1)


def _round(df: pd.DataFrame, n: int = 4) -> pd.DataFrame:
    num = df.select_dtypes("number").columns
    return df.assign(**{c: df[c].round(n) for c in num})


# --------------------------------------------------------------------------- vendors
def vendor_scorecard_monthly(inv: pd.DataFrame, dlv: pd.DataFrame) -> pd.DataFrame:
    spend = (inv.assign(month=_month(inv["invoice_date"]))
             .groupby(["vendor_id", "month"], as_index=False)
             .agg(spend=("line_amount", "sum"), invoice_lines=("line_amount", "size")))
    d = dlv[~dlv["is_open"]].assign(month=lambda x: _month(x["received_date"]))
    perf = d.groupby(["vendor_id", "month"], as_index=False).agg(
        deliveries=("po_number", "nunique"), lines=("po_number", "size"),
        on_time_rate=("on_time", "mean"), in_full_rate=("in_full", "mean"), otif_rate=("otif", "mean"),
        fill_rate=("fill_rate", "mean"), avg_days_late=("days_late", "mean"),
        lead_time_mean=("lead_time_days", "mean"), lead_time_sd=("lead_time_days", "std"))
    return _round(spend.merge(perf, on=["vendor_id", "month"], how="left"))


def billing_exceptions(inv: pd.DataFrame, dlv: pd.DataFrame) -> pd.DataFrame:
    billed = (inv[inv["line_type"] == "INV"].groupby(["po_ref", "vendor_sku", "vendor_id", "item_id"], as_index=False)
              .agg(invoice_number=("invoice_number", "first"), invoice_date=("invoice_date", "first"),
                   qty_billed=("qty", "sum"), unit_price=("unit_price", "mean")))
    m = billed.merge(dlv[["po_number", "vendor_sku", "qty_ordered", "qty_received"]],
                     left_on=["po_ref", "vendor_sku"], right_on=["po_number", "vendor_sku"], how="inner")
    m["qty_overbilled"] = (m["qty_billed"] - m["qty_received"]).clip(lower=0)
    m["amount_overbilled"] = (m["qty_overbilled"] * m["unit_price"]).round(2)
    return m.loc[m["qty_overbilled"] > 0, ["invoice_number", "invoice_date", "po_ref", "vendor_sku", "vendor_id", "item_id",
                                            "qty_ordered", "qty_received", "qty_billed", "qty_overbilled",
                                            "unit_price", "amount_overbilled"]].reset_index(drop=True)


def item_price_monthly(inv: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    x = inv[(inv["line_type"] == "INV") & (inv["vendor_id"] != BACKUP_VENDOR)].assign(month=_month(inv["invoice_date"]))
    x["cost"] = x["unit_cost_base"] * x["qty_base"]
    g = x.groupby(["item_id", "vendor_id", "month"], as_index=False).agg(cost=("cost", "sum"), qty_base=("qty_base", "sum"))
    g["avg_unit_cost"] = g["cost"] / g["qty_base"]
    g = g.merge(market, on=["item_id", "month"], how="left")
    g["premium_vs_market_pct"] = g["avg_unit_cost"] / g["benchmark_price"] - 1
    g = g.sort_values(["item_id", "vendor_id", "month"])
    grp = g.groupby(["item_id", "vendor_id"])["avg_unit_cost"]
    g["mom_change_pct"] = grp.pct_change(fill_method=None)
    g["yoy_change_pct"] = grp.pct_change(12, fill_method=None)
    return _round(g.drop(columns=["cost"]).reset_index(drop=True))


def vendor_risk(inv, dlv, prices, billing, as_of: pd.Timestamp) -> pd.DataFrame:
    """Explainable 0-100 risk score from six signals over the trailing 12 months."""
    last12 = as_of - pd.DateOffset(months=12)
    last6 = as_of - pd.DateOffset(months=6)
    i12 = inv[inv["invoice_date"] > last12]
    d = dlv[~dlv["is_open"]]
    d12 = d[d["received_date"] > last12]

    spend = i12.groupby("vendor_id")["line_amount"].sum()
    out = pd.DataFrame({"spend_12m": spend})
    out["spend_share"] = out["spend_12m"] / out["spend_12m"].sum()
    perf = d12.groupby("vendor_id").agg(on_time_rate=("on_time", "mean"), fill_rate=("fill_rate", "mean"),
                                       otif_rate=("otif", "mean"), lead_time_mean=("lead_time_days", "mean"),
                                       lead_time_sd=("lead_time_days", "std"), avg_days_late=("days_late", "mean"))
    out = out.join(perf)
    out["lead_time_cv"] = out["lead_time_sd"] / out["lead_time_mean"].replace(0, np.nan)
    out["days_late_sd"] = d12.groupby("vendor_id")["days_late"].std()

    recent = d[d["received_date"] > last6].groupby("vendor_id")["on_time"].mean()
    prior = d[(d["received_date"] > last6 - pd.DateOffset(months=6)) & (d["received_date"] <= last6)].groupby("vendor_id")["on_time"].mean()
    out["on_time_trend_6m"] = recent - prior

    p = prices.copy()
    p["month_ts"] = pd.PeriodIndex(p["month"], freq="M").to_timestamp()
    p12 = p[p["month_ts"] > last12]
    out["price_volatility"] = p12.groupby(["vendor_id", "item_id"])["mom_change_pct"].std().groupby("vendor_id").median()
    # spend-weighted YoY price change: avg cost last 12m vs the 12m before
    p["cost"] = p["avg_unit_cost"] * p["qty_base"]
    prior_window = (p["month_ts"] <= last12) & (p["month_ts"] > last12 - pd.DateOffset(months=12))

    def wavg(frame):
        g = frame.groupby(["vendor_id", "item_id"])[["cost", "qty_base"]].sum()
        return g["cost"] / g["qty_base"], g["cost"]

    cur, weight = wavg(p[p["month_ts"] > last12])
    prv, _ = wavg(p[prior_window])
    chg = (cur / prv - 1).dropna()
    ww = weight.reindex(chg.index)
    out["price_change_yoy"] = (chg * ww).groupby(level="vendor_id").sum() / ww.groupby(level="vendor_id").sum()

    b12 = billing[billing["invoice_date"] > last12].groupby("vendor_id")["amount_overbilled"].sum()
    out["overbilled_12m"] = b12.reindex(out.index).fillna(0)
    out["overbilling_rate"] = out["overbilled_12m"] / out["spend_12m"]
    out = out.drop(index=BACKUP_VENDOR, errors="ignore")  # walk-in store: not a contracted supplier

    # --- score components (0 = no risk, 1 = max risk), thresholds chosen to be explainable on camera
    comp = pd.DataFrame(index=out.index)
    comp["risk_delivery"] = _clip01((0.98 - out["on_time_rate"]) / 0.30)
    comp["risk_fill"] = _clip01((0.995 - out["fill_rate"]) / 0.08)
    comp["risk_lead_variability"] = _clip01(out["days_late_sd"].fillna(0) / 2.0)  # +/- 2 days of wobble = max
    comp["risk_price"] = _clip01(out["price_volatility"].fillna(0) / 0.08) * 0.5 + _clip01(out["price_change_yoy"].fillna(0) / 0.10) * 0.5
    comp["risk_dependency"] = _clip01(out["spend_share"] / 0.40)
    comp["risk_billing"] = _clip01(out["overbilling_rate"] / 0.01)
    weights = {"risk_delivery": 0.25, "risk_fill": 0.15, "risk_lead_variability": 0.10,
               "risk_price": 0.20, "risk_dependency": 0.20, "risk_billing": 0.10}
    out = out.join(comp)
    out["risk_score"] = sum(comp[c] * w for c, w in weights.items()) * 100
    out["risk_tier"] = pd.cut(out["risk_score"], [-1, 30, 50, 101], labels=["Low", "Medium", "High"]).astype(str)
    out["top_risk_driver"] = comp.mul(pd.Series(weights)).idxmax(axis=1).str.replace("risk_", "")
    out["as_of"] = as_of.normalize()
    return _round(out.reset_index().rename(columns={"index": "vendor_id"}))


# --------------------------------------------------------------------------- inventory
def inventory_weekly(counts: pd.DataFrame, dlv: pd.DataFrame, inv: pd.DataFrame, waste: pd.DataFrame) -> pd.DataFrame:
    """Nobody records usage in this kitchen. So we derive it:
    usage = last count + received since - logged waste since - this count."""
    rec = dlv[~dlv["is_open"]][["item_id", "received_date", "qty_received_base"]].rename(
        columns={"received_date": "date", "qty_received_base": "qty"})
    walk = inv[(inv["vendor_id"] == BACKUP_VENDOR) & (inv["line_type"] == "INV")][["item_id", "invoice_date", "qty_base"]]
    walk = walk.rename(columns={"invoice_date": "date", "qty_base": "qty"})
    receipts = pd.concat([rec, walk])
    rows = []
    for item_id, c in counts.sort_values("count_date").groupby("item_id"):
        r = receipts[receipts["item_id"] == item_id]
        w = waste[waste["item_id"] == item_id]
        dates, oh = list(pd.to_datetime(c["count_date"])), c["on_hand_base"].to_numpy()
        for k in range(1, len(c)):
            lo, hi = dates[k - 1], dates[k]
            received = r.loc[(r["date"] > lo) & (r["date"] <= hi), "qty"].sum()
            wasted = w.loc[(w["date"] > lo) & (w["date"] <= hi), "qty_base"].sum()
            days = (hi - lo).days
            usage = oh[k - 1] + received - wasted - oh[k]
            rows.append(dict(item_id=item_id, week_start=lo + pd.Timedelta(days=1), count_date=hi,
                             days=days, opening_on_hand=oh[k - 1], received=received, waste_logged=wasted,
                             closing_on_hand=oh[k], derived_usage=usage, daily_usage=usage / days,
                             negative_usage_flag=usage < 0))
    return _round(pd.DataFrame(rows))


def inventory_health(weekly, counts, dlv, inv, waste, items, vrisk, as_of) -> pd.DataFrame:
    last12 = as_of - pd.DateOffset(months=12)
    wk = weekly[weekly["count_date"] > last12]
    in_season = wk[~wk["count_date"].dt.month.isin([7, 8])]  # August handover: summer volumes would mislead
    use = in_season.groupby("item_id").agg(
        planning_daily_usage=("daily_usage", "mean"),
        weekly_usage_sd=("derived_usage", "std"))
    use["planning_daily_usage"] = use["planning_daily_usage"].clip(lower=0)
    use["daily_usage_sd"] = use["weekly_usage_sd"] / np.sqrt(7)

    latest = counts.sort_values("count_date").groupby("item_id").tail(1).set_index("item_id")
    out = items.set_index("item_id")[["item_name", "category", "base_uom", "shelf_life_days", "primary_vendor_id"]].join(use)
    out["on_hand"] = latest["on_hand_base"]
    out["last_count_date"] = latest["count_date"]

    d12 = dlv[(~dlv["is_open"]) & (dlv["received_date"] > last12)]
    lt = d12.groupby(["vendor_id"]).agg(lead_time_mean=("lead_time_days", "mean"), lead_time_sd=("lead_time_days", "std"))
    review = (dlv.groupby("vendor_id")["order_date"].apply(lambda s: s.drop_duplicates().sort_values().diff().dt.days.median())
              .rename("review_period_days"))
    out = out.join(lt, on="primary_vendor_id").join(review, on="primary_vendor_id")

    d, sd = out["planning_daily_usage"], out["daily_usage_sd"]
    L, sL, R = out["lead_time_mean"], out["lead_time_sd"].fillna(0), out["review_period_days"]
    out["safety_stock"] = Z_95 * np.sqrt((L + R) * sd ** 2 + d ** 2 * sL ** 2)
    out["reorder_point"] = d * L + out["safety_stock"]
    out["order_up_to_level"] = d * (L + R) + out["safety_stock"]
    out["days_of_cover"] = out["on_hand"] / d.replace(0, np.nan)
    # inventory position = on hand + already ordered but not yet arrived (what you compare to the ROP)
    cnt_date = out["last_count_date"].max()
    open_po = dlv[(dlv["order_date"] <= cnt_date) & (dlv["is_open"] | (dlv["received_date"] > cnt_date))]
    out["on_order"] = open_po.groupby("item_id")["qty_ordered_base"].sum().reindex(out.index).fillna(0)
    out["inventory_position"] = out["on_hand"] + out["on_order"]

    i12 = inv[inv["invoice_date"] > last12]
    out["spend_12m"] = i12.groupby("item_id")["line_amount"].sum()
    buys = i12[i12["line_type"] == "INV"].assign(cost=lambda x: x["unit_cost_base"] * x["qty_base"])
    g = buys.groupby("item_id")[["cost", "qty_base"]].sum()
    out["avg_unit_cost"] = g["cost"] / g["qty_base"]
    w12 = waste[waste["date"] > last12].groupby("item_id")["qty_base"].sum()
    out["waste_qty_12m"] = w12.reindex(out.index).fillna(0)
    out["waste_cost_12m"] = out["waste_qty_12m"] * out["avg_unit_cost"]
    used12 = wk.groupby("item_id")["derived_usage"].sum().clip(lower=0)
    out["waste_rate"] = out["waste_qty_12m"] / (used12 + out["waste_qty_12m"]).replace(0, np.nan)
    out["emergency_buys_12m"] = i12[i12["vendor_id"] == BACKUP_VENDOR].groupby("item_id").size().reindex(out.index).fillna(0).astype(int)

    # ABC: A = items making up the first 80% of spend
    s = out["spend_12m"].fillna(0).sort_values(ascending=False)
    cum = s.cumsum() / s.sum()
    out["abc_class"] = pd.Series(np.where(cum <= 0.80, "A", np.where(cum <= 0.95, "B", "C")), index=s.index)

    out = out.join(vrisk.set_index("vendor_id")[["risk_score"]].rename(columns={"risk_score": "vendor_risk_score"}),
                   on="primary_vendor_id")
    perishable = out["shelf_life_days"] < 60
    overstock_limit = np.where(perishable, out["shelf_life_days"], 90)
    out["stock_status"] = np.select(
        [out["inventory_position"] < out["reorder_point"], out["days_of_cover"] > overstock_limit],
        ["Below reorder point", "Overstocked"], "Healthy")

    comp = pd.DataFrame({
        "supply": out["vendor_risk_score"].fillna(0) / 100,
        "stockouts": _clip01(out["emergency_buys_12m"] / 6),
        "waste": _clip01(out["waste_rate"].fillna(0) / 0.25),
        "coverage": (out["stock_status"] == "Below reorder point").astype(float),
        "lead_variability": _clip01((sL / L).fillna(0) / 0.5),
    })
    weights = {"supply": 0.30, "stockouts": 0.25, "waste": 0.20, "coverage": 0.15, "lead_variability": 0.10}
    out["item_risk_score"] = sum(comp[c] * w for c, w in weights.items()) * 100
    out["top_risk_driver"] = comp.mul(pd.Series(weights)).idxmax(axis=1)
    out["recommended_action"] = [_action(r) for r in out.itertuples()]
    out["as_of"] = as_of.normalize()
    return _round(out.reset_index().rename(columns={"index": "item_id"}))


def _action(r) -> str:
    # First rule that fires wins, most urgent first. Every number is measured from this item's own data,
    # so the same rules produce different actions for different items, vendors, and business sizes.
    if r.stock_status == "Below reorder point":
        qty = max(r.order_up_to_level - r.inventory_position, 0)
        return (f"Reorder ~{qty:,.1f} {r.base_uom} now — stock incl. incoming ({r.inventory_position:,.1f}) "
                f"is under the reorder point ({r.reorder_point:,.1f})")
    if r.waste_rate is not None and r.waste_rate > 0.15:
        return f"Order less per delivery — {r.waste_rate:.0%} of what's bought is going to waste"
    if r.emergency_buys_12m >= 3:
        return f"Raise safety stock — {int(r.emergency_buys_12m)} emergency store runs in 12 months"
    if (r.vendor_risk_score or 0) >= 50:
        return "Line up a backup supplier — the primary vendor's risk score is High"
    if r.stock_status == "Overstocked":
        return f"Pause ordering — ~{r.days_of_cover:,.0f} days of stock already on hand"
    return "No action"


# --------------------------------------------------------------------------- summary + dims
def kpi_summary(inv, dlv, vrisk, health, billing, prices, as_of) -> pd.DataFrame:
    last12 = as_of - pd.DateOffset(months=12)
    prev12 = last12 - pd.DateOffset(months=12)
    s12 = inv.loc[inv["invoice_date"] > last12, "line_amount"].sum()
    sp = inv.loc[(inv["invoice_date"] > prev12) & (inv["invoice_date"] <= last12), "line_amount"].sum()
    d12 = dlv[(~dlv["is_open"]) & (dlv["received_date"] > last12)]
    rows = [
        ("Spend (last 12m)", s12, "CAD"),
        ("Spend change vs prior 12m", s12 / sp - 1 if sp else np.nan, "pct"),
        ("Spend-weighted price change YoY", np.average(vrisk["price_change_yoy"].fillna(0), weights=vrisk["spend_12m"]), "pct"),
        ("On-time delivery rate", d12["on_time"].mean(), "pct"),
        ("OTIF rate", d12["otif"].mean(), "pct"),
        ("Fill rate", d12["fill_rate"].mean(), "pct"),
        ("High-risk vendors", int((vrisk["risk_tier"] == "High").sum()), "count"),
        ("Items below reorder point", int((health["stock_status"] == "Below reorder point").sum()), "count"),
        ("Logged waste cost (last 12m)", health["waste_cost_12m"].sum(), "CAD"),
        ("Overbilled amount (last 12m)", billing.loc[billing["invoice_date"] > last12, "amount_overbilled"].sum(), "CAD"),
        ("Emergency walk-in buys (last 12m)", int(health["emergency_buys_12m"].sum()), "count"),
    ]
    df = pd.DataFrame(rows, columns=["metric", "value", "unit"])
    df["as_of"] = as_of.normalize()
    return _round(df)


def dim_date(start, end) -> pd.DataFrame:
    d = pd.DataFrame({"date": pd.date_range(start, end, freq="D")})
    d["year"] = d["date"].dt.year
    d["quarter"] = "Q" + d["date"].dt.quarter.astype(str)
    d["month"] = d["date"].dt.to_period("M").astype(str)
    d["month_name"] = d["date"].dt.strftime("%b")
    d["week_start"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    d["weekday"] = d["date"].dt.strftime("%a")
    d["is_school_term"] = ~d["date"].dt.month.isin([7, 8])
    return d


def build(s: dict[str, pd.DataFrame], storage) -> dict[str, pd.DataFrame]:
    inv, dlv = s["fact_invoice_line"], s["fact_delivery"]
    as_of = inv.loc[inv["line_type"] == "INV", "invoice_date"].max()  # last real delivery day
    prices = item_price_monthly(inv, s["market_benchmark"])
    billing = billing_exceptions(inv, dlv)
    vrisk = vendor_risk(inv, dlv, prices, billing, as_of)
    weekly = inventory_weekly(s["fact_inventory_count"], dlv, inv, s["fact_waste"])
    health = inventory_health(weekly, s["fact_inventory_count"], dlv, inv, s["fact_waste"], s["dim_item"], vrisk, as_of)
    out = dict(
        vendor_scorecard_monthly=vendor_scorecard_monthly(inv, dlv),
        vendor_risk=vrisk,
        billing_exceptions=billing,
        item_price_monthly=prices,
        inventory_weekly=weekly,
        inventory_health=health,
        kpi_summary=kpi_summary(inv, dlv, vrisk, health, billing, prices, as_of),
        dim_vendor=s["dim_vendor"],
        dim_item=s["dim_item"],
        dim_date=dim_date(inv["invoice_date"].min(), as_of),
    )
    for name, df in out.items():
        storage.write("gold", name, df)
    return out
