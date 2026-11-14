"""Silver = clean, typed, conformed data you can trust.

Rules of thumb used throughout:
  * every fact gets a canonical vendor_id + item_id
  * every quantity is converted to the item's base unit (kg / L / ea)
  * every price becomes cost per base unit
  * bad rows are never silently dropped: they go to a quarantine table with a reason
"""

from __future__ import annotations

import difflib
import re

import numpy as np
import pandas as pd

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%b %d %Y"]

UOM_MAP = {
    "lb": "LB", "lbs": "LB",
    "cs": "CS", "case": "CS",
    "kg": "KG", "bag": "BAG", "ea": "EA", "each": "EA",
    "dz": "DZ", "dozen": "DZ", "jug": "JUG", "l": "L", "ltr": "L",
}
LB_TO_KG = 0.45359237
_SUFFIXES = r"\b(inc|ltd|co|company|corp|limited)\b"


# --------------------------------------------------------------------------- helpers
def normalize_text(s: pd.Series) -> pd.Series:
    return (s.astype("string").str.lower().str.replace(r"\s+", " ", regex=True).str.strip())


def normalize_vendor_name(name: str) -> str:
    s = str(name).lower().replace("&", " and ")
    s = re.sub(r"[.\-]", "", s)             # G.T.A. -> gta
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(_SUFFIXES, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def build_vendor_matcher(vendor_master: pd.DataFrame):
    canon = {normalize_vendor_name(n): vid for n, vid in zip(vendor_master["vendor_name"], vendor_master["vendor_id"])}
    cache: dict[str, str | None] = {}

    def match(raw: str) -> str | None:
        if raw in cache:
            return cache[raw]
        n = normalize_vendor_name(raw)
        vid = canon.get(n)
        if vid is None:  # fuzzy fallback for typos / abbreviations
            best = difflib.get_close_matches(n, list(canon), n=1, cutoff=0.6)
            vid = canon[best[0]] if best else None
        cache[raw] = vid
        return vid

    return match


def parse_dates(s: pd.Series) -> pd.Series:
    """Try each known export format explicitly. Never guess day-vs-month."""
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for fmt in DATE_FORMATS:
        parsed = pd.to_datetime(s, format=fmt, errors="coerce")
        out = out.fillna(parsed)
    return out


def parse_money(s: pd.Series) -> pd.Series:
    cleaned = s.astype("string").str.replace(r"[$,\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def normalize_uom(s: pd.Series) -> pd.Series:
    low = s.astype("string").str.strip().str.lower()
    return low.map(UOM_MAP).fillna(low.str.upper())


def _quarantine(df: pd.DataFrame, mask: pd.Series, reason: str, source: str) -> pd.DataFrame:
    q = df.loc[mask].copy()
    q["_reason"] = reason
    q["_source_table"] = source
    return q


# --------------------------------------------------------------------------- dimensions
def build_dims(b: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    vendors = b["vendor_master"][["vendor_id", "vendor_name", "payment_terms", "primary_categories"]].copy()
    items = b["item_master"].drop(columns=["_source_file", "_ingested_at"]).copy()
    items["shelf_life_days"] = items["shelf_life_days"].astype(int)
    cat = b["vendor_catalog"].drop(columns=["_source_file", "_ingested_at"]).copy()
    cat["base_units_per_uom"] = cat["base_units_per_uom"].astype(float)
    return dict(dim_vendor=vendors, dim_item=items, vendor_catalog=cat)


# --------------------------------------------------------------------------- invoices
def clean_invoices(raw: pd.DataFrame, dims: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = raw.copy()
    quarantine = []
    match = build_vendor_matcher(dims["dim_vendor"])
    df["vendor_id"] = df["vendor_name"].map(match)
    df["invoice_date"] = parse_dates(df["invoice_date"])
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["unit_price"] = parse_money(df["unit_price"])
    df["line_total"] = parse_money(df["line_total"])
    df["uom"] = normalize_uom(df["uom"])

    # 1. exact re-sent lines
    key = ["invoice_number", "vendor_sku", "qty", "unit_price", "line_total", "line_type"]
    dup = df.duplicated(subset=key, keep="first")
    quarantine.append(_quarantine(df, dup, "duplicate_line", "invoices"))
    df = df.loc[~dup]

    # 2. impute missing unit price from line total (and flag it)
    df["price_imputed"] = df["unit_price"].isna() & df["line_total"].notna() & (df["qty"] != 0)
    df.loc[df["price_imputed"], "unit_price"] = df["line_total"] / df["qty"]

    # 3. attach catalog: SKU -> item + unit conversion, and cross-check vendor
    cat = dims["vendor_catalog"].rename(columns={"vendor_id": "catalog_vendor_id"})
    df = df.merge(cat[["vendor_sku", "catalog_vendor_id", "item_id", "invoice_uom", "base_units_per_uom"]],
                  on="vendor_sku", how="left")
    checks = [
        (lambda x: x["vendor_id"].isna(), "unknown_vendor"),
        (lambda x: x["item_id"].isna(), "unknown_sku"),
        (lambda x: x["vendor_id"] != x["catalog_vendor_id"], "vendor_sku_mismatch"),
        (lambda x: x["invoice_date"].isna(), "bad_date"),
        (lambda x: x["unit_price"].isna() | x["qty"].isna(), "missing_qty_or_price"),
        (lambda x: (x["qty"] < 0) & (x["line_type"] == "INV"), "negative_qty_on_invoice"),
        (lambda x: x["uom"] != x["invoice_uom"], "uom_mismatch"),
    ]
    for rule, reason in checks:  # each rule runs on what survived the previous one
        mask = rule(df).fillna(False).astype(bool)
        quarantine.append(_quarantine(df, mask, reason, "invoices"))
        df = df.loc[~mask]

    df["qty_base"] = df["qty"] * df["base_units_per_uom"]
    df["unit_cost_base"] = df["unit_price"] / df["base_units_per_uom"]
    df["line_amount"] = (df["qty"] * df["unit_price"]).round(2)

    # 4. price outliers (decimal-shift typos): compare to the item's rolling median
    df = df.sort_values("invoice_date")
    med = df.groupby("item_id")["unit_cost_base"].transform(lambda s: s.rolling(15, min_periods=3, center=True).median())
    ratio = df["unit_cost_base"] / med
    outlier = (ratio > 3) | (ratio < 1 / 3)
    quarantine.append(_quarantine(df, outlier, "price_outlier", "invoices"))
    df = df.loc[~outlier]

    cols = ["invoice_number", "invoice_date", "vendor_id", "po_ref", "vendor_sku", "item_id", "line_type",
            "qty", "uom", "qty_base", "unit_price", "unit_cost_base", "line_amount", "price_imputed", "_source_file"]
    return df[cols].reset_index(drop=True), pd.concat(quarantine, ignore_index=True)


# --------------------------------------------------------------------------- deliveries
def clean_deliveries(po_raw: pd.DataFrame, rec_raw: pd.DataFrame, dims: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    po = po_raw.copy()
    po["order_date"] = parse_dates(po["order_date"])
    po["expected_date"] = parse_dates(po["expected_date"])
    po["qty_ordered"] = pd.to_numeric(po["qty_ordered"], errors="coerce")
    po["uom"] = normalize_uom(po["uom"])
    po = po.drop_duplicates(subset=["po_number", "vendor_sku"])

    rec = rec_raw.copy()
    rec["received_date"] = parse_dates(rec["received_date"])
    rec["qty_received"] = pd.to_numeric(rec["qty_received"], errors="coerce")
    rec = rec.drop_duplicates(subset=["po_number", "vendor_sku"])

    cat = dims["vendor_catalog"][["vendor_sku", "item_id", "base_units_per_uom"]]
    d = po.merge(rec[["po_number", "vendor_sku", "received_date", "qty_received", "notes"]],
                 on=["po_number", "vendor_sku"], how="left").merge(cat, on="vendor_sku", how="left")

    q = _quarantine(d, d["item_id"].isna(), "unknown_sku", "deliveries")
    d = d.loc[d["item_id"].notna()].copy()
    d["is_open"] = d["received_date"].isna()
    d["qty_ordered_base"] = d["qty_ordered"] * d["base_units_per_uom"]
    d["qty_received_base"] = d["qty_received"] * d["base_units_per_uom"]
    d["days_late"] = (d["received_date"] - d["expected_date"]).dt.days
    d["lead_time_days"] = (d["received_date"] - d["order_date"]).dt.days
    d["on_time"] = d["days_late"] <= 0
    d["fill_rate"] = (d["qty_received"] / d["qty_ordered"]).clip(upper=1)
    d["in_full"] = d["qty_received"] >= d["qty_ordered"]
    d["otif"] = d["on_time"] & d["in_full"]
    cols = ["po_number", "vendor_id", "item_id", "vendor_sku", "order_date", "expected_date", "received_date",
            "qty_ordered", "qty_received", "qty_ordered_base", "qty_received_base", "days_late",
            "lead_time_days", "on_time", "in_full", "otif", "fill_rate", "is_open"]
    return d[cols].reset_index(drop=True), q


# --------------------------------------------------------------------------- counts & waste
def _alias_lookup(b: dict) -> dict[str, str]:
    a = b["item_aliases"]
    return dict(zip(normalize_text(a["alias"]), a["item_id"]))


def _to_base(qty: pd.Series, uom: pd.Series, item_id: pd.Series, dims: dict) -> pd.Series:
    """Hand-counted units -> base units. Cases/bags/jugs use the primary vendor's pack size."""
    items = dims["dim_item"].set_index("item_id")
    cat = dims["vendor_catalog"]
    pack = cat.merge(items[["primary_vendor_id"]], left_on=["item_id", "vendor_id"],
                     right_on=["item_id", "primary_vendor_id"]).set_index("item_id")["base_units_per_uom"]
    base_uom = item_id.map(items["base_uom"]).str.upper()
    uom = uom.astype(object)
    base_uom = base_uom.astype(object)
    factor = pd.Series(np.nan, index=qty.index)
    factor[(uom == base_uom).to_numpy(bool)] = 1.0
    factor[((uom == "LB") & (base_uom == "KG")).to_numpy(bool)] = LB_TO_KG
    is_pack = uom.isin(["CS", "BAG", "JUG", "DZ"]).to_numpy(bool)
    factor[is_pack] = item_id[is_pack].map(pack).to_numpy(float)
    return qty * factor


def clean_counts(raw: pd.DataFrame, b: dict, dims: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = raw.copy()
    lookup = _alias_lookup(b)
    df["item_id"] = normalize_text(df["item"]).map(lookup)
    df["count_date"] = parse_dates(df["count_date"])
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["uom"] = normalize_uom(df["uom"])
    q = []
    for mask, reason in [(df["item_id"].isna(), "unmapped_item_name"), (df["qty"].isna(), "blank_count")]:
        q.append(_quarantine(df, mask, reason, "inventory_counts"))
        df = df.loc[~mask]
    df["on_hand_base"] = _to_base(df["qty"], df["uom"], df["item_id"], dims)
    bad = df["on_hand_base"].isna()
    q.append(_quarantine(df, bad, "unconvertible_uom", "inventory_counts"))
    df = df.loc[~bad]
    # two people counted the same item the same day -> average them
    df = (df.groupby(["count_date", "item_id"], as_index=False)
            .agg(on_hand_base=("on_hand_base", "mean"), counted_by=("counted_by", "first")))
    return df, pd.concat(q, ignore_index=True)


def clean_waste(raw: pd.DataFrame, b: dict, dims: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = raw.copy()
    df["item_id"] = normalize_text(df["item"]).map(_alias_lookup(b))
    df["date"] = parse_dates(df["date"])
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["uom"] = normalize_uom(df["uom"])
    bad = df["item_id"].isna() | df["qty"].isna()
    q = _quarantine(df, bad, "unmapped_or_blank", "waste_log")
    df = df.loc[~bad].copy()
    df["qty_base"] = _to_base(df["qty"], df["uom"], df["item_id"], dims)
    return df[["date", "item_id", "qty_base", "reason"]].reset_index(drop=True), q


def clean_market(raw: pd.DataFrame) -> pd.DataFrame:
    cols = ["month", "item_id", "benchmark_price"] + (["source"] if "source" in raw else [])
    df = raw[cols].copy()
    df["benchmark_price"] = pd.to_numeric(df["benchmark_price"])
    if "source" not in df:
        df["source"] = "simulated"
    df = df.rename(columns={"source": "benchmark_source"})
    return df


# --------------------------------------------------------------------------- orchestrate
def build(b: dict[str, pd.DataFrame], storage) -> dict[str, pd.DataFrame]:
    dims = build_dims(b)
    invoices, q_inv = clean_invoices(b["invoices"], dims)
    deliveries, q_del = clean_deliveries(b["purchase_orders"], b["receiving"], dims)
    counts, q_cnt = clean_counts(b["inventory_counts"], b, dims)
    waste, q_w = clean_waste(b["waste_log"], b, dims)
    market = clean_market(b["market_benchmark"])
    quarantine = pd.concat([q_inv, q_del, q_cnt, q_w], ignore_index=True)
    quarantine = quarantine.astype("string")  # mixed schemas -> keep it simple for storage

    out = dict(**dims, fact_invoice_line=invoices, fact_delivery=deliveries,
               fact_inventory_count=counts, fact_waste=waste, market_benchmark=market,
               quarantine=quarantine)
    for name, df in out.items():
        storage.write("silver", name, df)
    return out
