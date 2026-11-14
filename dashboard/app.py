"""Local dashboard over the gold tables. Run:  streamlit run dashboard/app.py

This is the "see it before Power BI" view. The same gold tables feed Power BI in Episode 2.
Set VIP_LIVE=1 to simulate through yesterday with real StatCan prices (that's what the hosted demo does).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))  # so checkdata imports even without pip install
DEFAULT_DATA = Path(os.environ.get("VIP_DATA", ROOT / "data"))
LIVE = os.environ.get("VIP_LIVE") == "1"

# ---- palette (validated reference palette: categorical order fixed, status colors reserved)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
STATUS = {"Low": "#0ca30c", "Medium": "#fab219", "High": "#d03b3b",
          "Healthy": "#0ca30c", "Overstocked": "#fab219", "Below reorder point": "#d03b3b"}
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"

st.set_page_config(page_title="Vendor & Inventory Risk", layout="wide")

OWN = "data_base" in st.session_state  # viewing the user's own data instead of the demo
DATA = Path(st.session_state["data_base"]) if OWN else DEFAULT_DATA
GOLD = DATA / "gold"


def style(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=36, b=8), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif",
                                                              color=INK2, size=13),
                      legend=dict(orientation="h", yanchor="top", y=-0.12, x=0, title=None),
                      hoverlabel=dict(bgcolor="white", font_color=INK), title_font=dict(color=INK, size=15))
    fig.update_xaxes(showgrid=False, linecolor=BASE, tickfont=dict(color=MUTED), title=None)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=BASE, tickfont=dict(color=MUTED), title=None)
    fig.update_traces(selector=dict(type="scatter"), line=dict(width=2))
    fig.update_traces(selector=dict(type="bar"), marker_line_width=0)
    return fig


SCALE = float(os.environ.get("VIP_SCALE", "1"))  # default business size for the first run / refresh


def run_pipeline(scale: float = SCALE):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    land = [sys.executable, "-m", "vendor_pipeline.jobs", "land", "--env", "local", "--base", str(DATA),
            "--scale", str(scale)]
    if LIVE:
        land += ["--end", "today", "--market", "auto"]
    for cmd in (land,
                [sys.executable, "-m", "vendor_pipeline.jobs", "run", "--env", "local", "--base", str(DATA)],
                [sys.executable, "-m", "vendor_pipeline.validate", "--base", str(DATA)]):
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if r.returncode not in (0, 1):  # validate exits 1 on a failed gate; still show the dashboard
            st.error(f"`{' '.join(cmd[2:5])}` failed:\n\n```\n{r.stderr[-2000:]}\n```")
            st.stop()
    st.cache_data.clear()


@st.cache_data(ttl=3600)
def load(name: str, _mtime: float) -> pd.DataFrame:
    return pd.read_parquet(GOLD / f"{name}.parquet")


def table(name: str) -> pd.DataFrame:
    f = GOLD / f"{name}.parquet"
    return load(name, f.stat().st_mtime) if f.exists() else pd.DataFrame()


if not (GOLD / "kpi_summary.parquet").exists():
    if OWN:
        st.error(f"No gold tables in {GOLD}. Run the pipeline on this folder first (sidebar, or "
                 f"`vip-run-pipeline --env local --base {DATA}`).")
        if st.button("Back to demo data"):
            del st.session_state["data_base"]
            st.rerun()
        st.stop()
    with st.spinner("First run: generating data and running the pipeline (≈15s)…"):
        run_pipeline()

kpi = table("kpi_summary")
vr = table("vendor_risk").merge(table("dim_vendor")[["vendor_id", "vendor_name"]], on="vendor_id")
sc = table("vendor_scorecard_monthly").merge(table("dim_vendor")[["vendor_id", "vendor_name"]], on="vendor_id")
prices = table("item_price_monthly").merge(table("dim_item")[["item_id", "item_name", "category"]], on="item_id")
health = table("inventory_health")
weekly = table("inventory_weekly")
dq = table("dq_results")
val = table("validation_metrics")
as_of = pd.to_datetime(kpi["as_of"].iloc[0])

# ---------------------------------------------------------------- header
with st.sidebar:
    st.markdown("### Vendor & Inventory Risk")
    st.caption(f"Data as of **{as_of:%b %d, %Y}**" + (" · live mode" if LIVE else ""))
    src = prices.drop_duplicates("item_id")["benchmark_source"].fillna("simulated")
    n_real = (~src.str.startswith("simulated")).sum()
    st.caption(f"Market benchmark: **{n_real}/{len(src)} items on real StatCan prices**" if n_real
               else "Market benchmark: simulated (StatCan not reachable)")
    if OWN:
        st.info(f"Viewing your own data:\n\n`{DATA}`")
        if st.button("Back to demo data", use_container_width=True):
            del st.session_state["data_base"]
            st.cache_data.clear()
            st.rerun()
    else:
        SIZES = {"Original kitchen (~$335k/yr)": 1.0, "Small business (<$100k/yr)": 0.25,
                 "Larger operation (~$1M/yr)": 3.0}
        nearest = min(SIZES, key=lambda k: abs(SIZES[k] - SCALE))
        size = st.selectbox("Business size (applied on refresh)", list(SIZES), index=list(SIZES).index(nearest),
                            help="Scales every item's demand in the simulator. The pipeline and metrics are "
                                 "identical — only the size of the business changes.")
        if st.button("Refresh data (re-run pipeline)", use_container_width=True):
            with st.spinner("Running land → bronze → silver → gold → validate…"):
                run_pipeline(SIZES[size])
            st.rerun()
    st.divider()
    with st.expander("Use your own data"):
        st.markdown(
            "Analyze **your** vendor exports instead of the demo — everything stays on this machine.\n\n"
            "1. Make a folder with a `raw/` subfolder holding your files in the contract layout. "
            "The demo's `data/raw/` is a working template of every file and column; the full guide is "
            "[docs/use-your-own-data.md](https://github.com/ssivasa10033/Vendor-Inventory-Risk-Pipeline/blob/main/docs/use-your-own-data.md).\n"
            "2. Check it, fix anything marked ❌, then run."
        )
        own_path = st.text_input("Folder containing raw/", placeholder=r"C:\my-business-data")
        if st.button("1 · Check my data", use_container_width=True, disabled=not own_path.strip()):
            from vendor_pipeline.checkdata import check as check_contract
            st.session_state["own_checks"] = (own_path.strip(), check_contract(own_path.strip()))
        if "own_checks" in st.session_state:
            checked_path, results = st.session_state["own_checks"]
            for r in results:
                st.caption({"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[r["level"]]
                           + f" **{r['source']}** — {r['message']}")
            fails = sum(r["level"] == "FAIL" for r in results)
            if fails:
                st.error(f"{fails} blocking problem(s). Fix them and check again.")
            elif st.button("2 · Run pipeline & view my data", use_container_width=True, type="primary"):
                with st.spinner("bronze → silver → gold + quality gate…"):
                    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
                    r = subprocess.run([sys.executable, "-m", "vendor_pipeline.jobs", "run",
                                        "--env", "local", "--base", checked_path],
                                       env=env, capture_output=True, text=True)
                if r.returncode != 0:
                    st.error(f"Pipeline failed:\n\n```\n{(r.stderr or r.stdout)[-1500:]}\n```")
                else:
                    st.session_state["data_base"] = checked_path
                    st.cache_data.clear()
                    st.rerun()
    st.divider()
    st.caption("Your data never leaves this machine." if OWN else
               "Transactions are simulated; the original business data is private. "
               "Market prices come from Statistics Canada when reachable.")

# ---------------------------------------------------------------- intro: what this is, before any numbers
hist_start, hist_end = str(sc["month"].min()), str(sc["month"].max())
n_vendors, n_items, n_months = vr["vendor_id"].nunique(), health["item_id"].nunique(), sc["month"].nunique()
with st.expander(f"Current data: {n_vendors} vendors · {n_items} items · "
                 f"{n_months} months ({hist_start} → {hist_end})"):
    if OWN:
        st.markdown(f"""
**Your data**, read from `{DATA}` and processed by the same pipeline as the demo:
`raw files → bronze (exact copy) → silver (cleaned & standardized; bad rows quarantined with a reason,
never deleted) → gold (the tables behind every chart here)`. Rows the cleaner couldn't confidently fix
are listed on the **Data quality** tab — that tab is the first thing to review with your own data.

| Tab | The question it answers |
|---|---|
| **Overview** | How is the business doing, and what needs attention this week? |
| **Vendors** | Which suppliers are a risk, why, and are they getting better or worse? |
| **Prices vs market** | Are we overpaying compared to the benchmark in your market file? |
| **Inventory** | What do we actually use, and what should we reorder today? |
| **Data quality & accuracy** | What couldn't be cleaned, and why? |
""")
    else:
        st.markdown(f"""
**The story.** A small catering kitchen + café changed owners. The previous owner left messy exports —
invoices, purchase orders, a receiving log, weekly hand counts, a half-filled waste log. This dashboard is
the end of a pipeline that cleans those files and turns them into three answers: *which suppliers are risky,
where money is leaking, and what to reorder.*

**The data.** Transactions are **simulated** (the real business's data is private) but every mess in them is
real: one vendor spelled four ways, four date formats in one column, pounds mixed with kilograms, duplicate
invoices. Market benchmark prices are **real Statistics Canada open data** when the sidebar says so.
History runs **{hist_start} to {hist_end}**, so every trend chart has {n_months} months behind it.

**How the numbers get here.** `raw files → bronze (exact copy) → silver (cleaned & standardized; bad rows
quarantined, never deleted) → gold (the tables behind every chart on this page)`. A quality gate checks every
run, and the pipeline's accuracy is scored against the simulator's hidden answer key — see the last tab.

| Tab | The question it answers |
|---|---|
| **Overview** | How is the business doing, and what needs attention this week? |
| **Vendors** | Which suppliers are a risk, why, and are they getting better or worse? |
| **Prices vs market** | Are we overpaying compared to the real market? |
| **Inventory** | What do we actually use, and what should we reorder today? |
| **Data quality & accuracy** | Why should anyone trust these numbers? |
""")

tabs = st.tabs(["Overview", "Vendors", "Prices vs market", "Inventory", "Data quality & accuracy"])


def kv(metric):
    r = kpi.set_index("metric").loc[metric]
    return r["value"], r["unit"]


def fmt(v, unit):
    return f"${v:,.0f}" if unit == "CAD" else f"{v:.1%}" if unit == "pct" else f"{int(v):,}"


# ---------------------------------------------------------------- overview
HELP = {
    "Spend (last 12m)": "Everything paid to vendors in the last 12 months, from cleaned invoice lines. "
                        "The denominator for most other numbers.",
    "Spend-weighted price change YoY": "Our ingredient inflation: how much unit prices rose vs a year ago, "
                                       "weighted by what we actually spend. Compare it to the market tab — "
                                       "rising WITH the market is inflation; rising faster is a vendor problem.",
    "OTIF rate": "On Time, In Full: the share of deliveries that arrived BOTH on the promised day AND complete. "
                 "The strictest service measure — a late OR short delivery both fail it.",
    "Logged waste cost (last 12m)": "Dollars of stock staff recorded as spoiled or discarded. Understated on "
                                    "purpose: staff only log ~75% of waste. The rest shows up as usage (shrink).",
    "High-risk vendors": "Vendors whose 0–100 risk score lands in the High tier. The score blends delivery, "
                         "price, dependency, fill rate, lead-time wobble and overbilling — see the Vendors tab.",
    "Items below reorder point": "Items whose stock position (on hand + already on order) is below the level "
                                 "where you must order NOW to avoid running out. Each one has an action on the right.",
    "Overbilled amount (last 12m)": "Dollars invoiced for product that never arrived (billed qty > received qty). "
                                    "Found by matching every invoice line to the receiving log.",
    "Emergency walk-in buys (last 12m)": "Times staff ran to a cash & carry because something ran out mid-week. "
                                         "The only stockout evidence in the data — and the cost of thin safety stock.",
}
with tabs[0]:
    cols = st.columns(4)
    for c, m in zip(cols, ["Spend (last 12m)", "Spend-weighted price change YoY", "OTIF rate", "Logged waste cost (last 12m)"]):
        c.metric(m, fmt(*kv(m)), help=HELP[m])
    cols = st.columns(4)
    for c, m in zip(cols, ["High-risk vendors", "Items below reorder point", "Overbilled amount (last 12m)",
                           "Emergency walk-in buys (last 12m)"]):
        c.metric(m, fmt(*kv(m)), help=HELP[m])
    st.caption("Hover the ⓘ on any tile for what it means and why it matters. "
               "All eight are recomputed from the gold tables on every pipeline run.")

    left, right = st.columns([3, 2])
    spend = sc.groupby("month", as_index=False)["spend"].sum()
    fig = px.bar(spend, x="month", y="spend", title="Monthly spend (all vendors, CAD)", color_discrete_sequence=SERIES)
    fig.update_traces(hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>")
    left.plotly_chart(style(fig), use_container_width=True)
    acts = health[health["recommended_action"] != "No action"].sort_values("item_risk_score", ascending=False)
    right.markdown("**What to do this week**")
    right.dataframe(acts[["recommended_action", "item_name", "abc_class"]].rename(
        columns={"item_name": "Item", "abc_class": "ABC", "recommended_action": "Action"}),
        hide_index=True, use_container_width=True, height=330,
        column_config={"Action": st.column_config.TextColumn(width="large")})
    right.caption("Each item gets the first rule that fires, most urgent first: below reorder point → "
                  "high waste → repeated emergency buys → risky primary vendor → overstocked. Every number "
                  "in an action is measured from that item's own history, so actions change as the data does.")

# ---------------------------------------------------------------- vendors
with tabs[1]:
    v = vr.sort_values("risk_score")
    fig = go.Figure(go.Bar(x=v["risk_score"], y=v["vendor_name"], orientation="h",
                           marker_color=[STATUS[t] for t in v["risk_tier"]],
                           text=[f"{s:.0f} · {t} · {d}" for s, t, d in zip(v["risk_score"], v["risk_tier"], v["top_risk_driver"])],
                           textposition="outside", textfont=dict(color=INK2),
                           hovertemplate="%{y}: %{x:.1f}<extra></extra>"))
    fig.update_layout(title="Vendor risk score (0–100) · tier · top driver", xaxis_range=[0, 100])
    st.plotly_chart(style(fig, 330), use_container_width=True)

    top_dep = vr.loc[vr["spend_share"].idxmax()]
    with st.expander("How the risk score is built (and why a good performer can still be High risk)"):
        st.markdown(f"""
Each vendor gets a 0–100 score from six signals, each measured from the cleaned data and scaled 0–1.
The weights are fixed and written down — a judgment call made visible, not a black box. The per-signal
columns in the table below show exactly which signal drives each vendor's score.

| Signal | Weight | What it measures, in plain terms |
|---|---|---|
| Delivery reliability | 25% | How often deliveries arrive on the promised day, and whether that's trending worse |
| Price | 20% | How jumpy their prices are week to week, and how much they rose vs last year |
| **Dependency** | 20% | **How much of our total spend goes to them.** Not about their performance at all — if one vendor gets a huge share of the money, their failure becomes our failure |
| Fill rate | 15% | How often they ship the full quantity ordered, not a short load |
| Lead-time variability | 10% | Whether orders take a predictable number of days or wobble — wobble forces bigger safety stock |
| Overbilling | 10% | Dollars billed for product the receiving log says never arrived |

**Dependency is the one people miss.** Right now **{top_dep["vendor_name"]}** takes
**{top_dep["spend_share"]:.0%} of all spend** — decent delivery numbers, but if they raise prices or fail,
there's no quick substitute. That's why they can out-score a sloppier but smaller vendor.
""")

    default = list(vr.sort_values("risk_score", ascending=False)["vendor_name"].head(3))
    pick = st.multiselect("Vendors to trend", sorted(vr["vendor_name"]), default=default, max_selections=4)
    c1, c2 = st.columns([3, 1])
    metric = c1.radio("Metric", ["on_time_rate", "otif_rate", "fill_rate", "avg_days_late"], horizontal=True,
                      format_func=lambda m: {"otif_rate": "OTIF rate"}.get(m, m.replace("_", " ").capitalize()))
    smooth = c2.toggle("3-month rolling", value=True)
    # color follows the vendor (fixed by vendor_id), never its rank or the current selection
    color_map = {n: SERIES[i] for i, n in enumerate(vr.sort_values("vendor_id")["vendor_name"])}
    t = sc[sc["vendor_name"].isin(pick)].sort_values("month").copy()
    if smooth:
        t[metric] = t.groupby("vendor_name")[metric].transform(lambda s: s.rolling(3, min_periods=1).mean())
    fig = px.line(t, x="month", y=metric, color="vendor_name", color_discrete_map=color_map, markers=False,
                  title=f"{metric.replace('_', ' ').capitalize()} by month" + (" (3-month rolling)" if smooth else ""))
    if metric != "avg_days_late":
        fig.update_yaxes(tickformat=".0%")
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(style(fig), use_container_width=True)
    st.dataframe(vr[["vendor_name", "risk_score", "risk_tier", "spend_share", "on_time_rate", "fill_rate",
                     "on_time_trend_6m", "price_change_yoy", "overbilled_12m", "risk_delivery", "risk_fill",
                     "risk_lead_variability", "risk_price", "risk_dependency", "risk_billing"]]
                 .sort_values("risk_score", ascending=False), hide_index=True, use_container_width=True,
                 column_config={
                     "vendor_name": "Vendor", "risk_score": st.column_config.NumberColumn("Risk", format="%.0f"),
                     "risk_tier": "Tier",
                     **{c: st.column_config.NumberColumn(c.replace("_", " ").capitalize(), format="percent")
                        for c in ["spend_share", "on_time_rate", "fill_rate", "on_time_trend_6m", "price_change_yoy"]},
                     "overbilled_12m": st.column_config.NumberColumn("Overbilled 12m", format="dollar"),
                     **{c: st.column_config.ProgressColumn(c.replace("risk_", "").replace("_", " "), min_value=0,
                                                           max_value=1, format="%.2f")
                        for c in ["risk_delivery", "risk_fill", "risk_lead_variability", "risk_price",
                                  "risk_dependency", "risk_billing"]}})

# ---------------------------------------------------------------- prices
with tabs[2]:
    st.caption("Blue = the average unit cost we actually paid that month (from cleaned invoices). "
               "Orange = a market benchmark built from real Statistics Canada price movement: retail shelf "
               "prices aren't what a caterer pays a distributor, so the benchmark anchors at the Jan-2023 "
               "wholesale price and moves with the real StatCan series from there. A growing gap means we're "
               "drifting above the market — time to renegotiate or switch.")
    names = prices.drop_duplicates("item_id").sort_values("item_name")
    item = st.selectbox("Item", names["item_name"], index=int((names["item_id"] == "M03").to_numpy().argmax()))
    p = prices[prices["item_name"] == item].sort_values("month")
    fig = go.Figure()
    fig.add_scatter(x=p["month"], y=p["avg_unit_cost"], name="What we paid", line_color=SERIES[0],
                    hovertemplate="%{x}: $%{y:.2f}<extra>paid</extra>")
    fig.add_scatter(x=p["month"], y=p["benchmark_price"], name="Market benchmark", line_color=SERIES[1],
                    line_dash="dot", hovertemplate="%{x}: $%{y:.2f}<extra>market</extra>")
    fig.update_layout(title=f"{item}: cost per base unit vs market (CAD)", hovermode="x unified")
    st.plotly_chart(style(fig), use_container_width=True)
    st.caption(f"Benchmark source: {p['benchmark_source'].dropna().iloc[-1] if p['benchmark_source'].notna().any() else 'simulated'}")
    last = prices.sort_values("month").groupby("item_id").tail(1)
    st.dataframe(last[["item_name", "category", "month", "avg_unit_cost", "benchmark_price", "premium_vs_market_pct",
                       "yoy_change_pct", "benchmark_source"]].sort_values("yoy_change_pct", ascending=False),
                 hide_index=True, use_container_width=True,
                 column_config={"item_name": "Item", "avg_unit_cost": st.column_config.NumberColumn("Paid / unit", format="$%.2f"),
                                "benchmark_price": st.column_config.NumberColumn("Market / unit", format="$%.2f"),
                                "premium_vs_market_pct": st.column_config.NumberColumn("Premium vs market", format="percent"),
                                "yoy_change_pct": st.column_config.NumberColumn("YoY change", format="percent"),
                                "benchmark_source": "Benchmark source"})

# ---------------------------------------------------------------- inventory
with tabs[3]:
    counts = health["stock_status"].value_counts()
    c = st.columns(3)
    for col, s in zip(c, ["Below reorder point", "Overstocked", "Healthy"]):
        col.metric(s, int(counts.get(s, 0)))
    with st.expander("How these numbers are calculated (nobody at the kitchen records usage — so it's rebuilt)"):
        st.markdown("""
* **Derived usage** — the kitchen never logs what it uses, so each week it's reconstructed from what *is*
  recorded: `last week's count + deliveries − logged waste − this week's count`. Whatever's unaccounted for
  (unlogged waste, shrink) lands in usage, which is the honest place for it. Validation shows this
  reconstruction tracks true consumption within ~5%.
* **Safety stock** — the buffer that absorbs a busy week or a late truck, sized from each item's *measured*
  usage swings and each vendor's *measured* lead-time wobble (95% service level). Nothing is assumed.
* **Reorder point** — expected usage during a resupply cycle + safety stock. Compared against inventory
  **position** (on hand **+ already on order**), so an item isn't flagged when a fix is already on the truck.
* **Days of cover** — how long current stock lasts at the recent usage rate.
* **ABC class** — A items are the few that take most of the spend (watch weekly); C items are cheap
  long-tail (check monthly).
* **Recommended action** — each item's numbers reduced to one instruction: reorder now (with quantity),
  pause ordering, cut pack size (high spoilage), or raise safety stock (repeated emergency buys).
* Planning usage **excludes July–August**: the handover happened in late August, and summer is the slow
  season — planning September off summer numbers would starve the kitchen.
""")
    cats = st.multiselect("Category", sorted(health["category"].unique()), default=sorted(health["category"].unique()))
    h = health[health["category"].isin(cats)].sort_values("item_risk_score", ascending=False)
    st.dataframe(h[["item_name", "category", "abc_class", "stock_status", "on_hand", "on_order", "reorder_point",
                    "order_up_to_level", "days_of_cover", "waste_rate", "emergency_buys_12m", "item_risk_score",
                    "recommended_action"]], hide_index=True, use_container_width=True,
                 column_config={"item_name": "Item", "abc_class": "ABC", "stock_status": "Status",
                                **{c: st.column_config.NumberColumn(c.replace("_", " ").capitalize(), format="%.1f")
                                   for c in ["on_hand", "on_order", "reorder_point", "order_up_to_level", "days_of_cover"]},
                                "waste_rate": st.column_config.NumberColumn("Waste rate", format="percent"),
                                "emergency_buys_12m": "Emergency buys 12m",
                                "item_risk_score": st.column_config.ProgressColumn("Risk", min_value=0, max_value=100, format="%.0f"),
                                "recommended_action": st.column_config.TextColumn("Action", width="large")})
    item = st.selectbox("Weekly usage for", h["item_name"])
    iid = health.loc[health["item_name"] == item, "item_id"].iloc[0]
    w = weekly[weekly["item_id"] == iid].sort_values("count_date").tail(52)  # last 52 weekly counts
    fig = go.Figure()
    fig.add_scatter(x=w["count_date"], y=w["derived_usage"], name="Derived weekly usage", line_color=SERIES[0])
    fig.add_scatter(x=w["count_date"], y=w["closing_on_hand"], name="On hand at count", line_color=SERIES[1])
    rop = health.loc[health["item_id"] == iid, "reorder_point"].iloc[0]
    fig.add_hline(y=rop, line_dash="dash", line_color=MUTED, annotation_text="reorder point",
                  annotation_font_color=INK2)
    fig.update_layout(title=f"{item}: last 52 weeks, usage rebuilt from counts + receipts − waste ({health.loc[health['item_id'] == iid, 'base_uom'].iloc[0]})",
                      hovermode="x unified")
    st.plotly_chart(style(fig), use_container_width=True)

# ---------------------------------------------------------------- data quality + accuracy
with tabs[4]:
    st.caption("Every pipeline run passes a quality gate: rows the cleaner can't confidently fix are "
               "quarantined with a reason — visible below, never silently dropped — and the run fails if too "
               "many land there." + ("" if OWN else " And because the demo data is simulated, the true answers "
               "exist in a hidden folder the pipeline never reads; vip-validate scores the pipeline against "
               "that answer key (right table). With real data, the quality gate still protects you."))
    q = pd.read_parquet(DATA / "silver" / "quarantine.parquet")
    qq = q.groupby(["_source_table", "_reason"], as_index=False).size().sort_values("size")
    fig = px.bar(qq, x="size", y="_reason", orientation="h", color_discrete_sequence=SERIES,
                 title="Rows quarantined (kept for review, never silently dropped)", hover_data=["_source_table"])
    st.plotly_chart(style(fig, 300), use_container_width=True)
    left, right = st.columns(2)
    left.markdown("**Pipeline quality gate (every run)**")
    left.dataframe(dq[["check", "level", "passed", "detail"]], hide_index=True, use_container_width=True)
    right.markdown("**Accuracy vs hidden ground truth (`vip-validate`)**")
    if len(val):
        right.dataframe(val[["area", "metric", "value", "threshold", "passed"]], hide_index=True, use_container_width=True,
                        column_config={"value": st.column_config.NumberColumn(format="%.3f"),
                                       "metric": st.column_config.TextColumn(width="large")})
        report = DATA / "validation_report.md"
        if report.exists():
            with st.expander("Full validation report"):
                st.markdown(report.read_text(encoding="utf-8"))
    elif OWN:
        right.info("Accuracy-vs-truth scoring only exists for simulated data (it needs the hidden answer key). "
                   "For your own data, trust comes from the quality gate on the left and from reconciling "
                   "cleaned + quarantined spend against your accounting totals.")
    else:
        right.info("Run `vip-validate` to score the pipeline against ground truth.")
