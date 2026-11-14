"""Simulate ~2.5 years of kitchen operations and export them the messy way real
small-business systems do.

The simulation runs a day-by-day inventory model:
  demand (seasonal, weekday-heavy) -> consumption / stockouts -> waste ->
  periodic-review ordering per vendor -> deliveries (late / short) -> invoices
  -> weekly hand counts.

The *true* state (actual usage, true waste) is never exported, just like real life:
the pipeline has to reconstruct consumption from counts, receipts and the waste log.

Messiness injected on export (on purpose, for the pipeline to clean):
  * vendor names typed four different ways
  * mixed date formats, "$1,234.50" price strings, inconsistent unit codes
  * invoices re-sent (duplicate lines), missing prices, decimal-shift typos
  * vendors billing for product they shorted
  * count sheets with nicknames for items, counts in cases/lb instead of kg
  * a waste log the staff only fills in some of the time
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .config import (
    BACKUP_VENDOR,
    BEEF_EXTRA_2025,
    CATEGORY_INFLATION,
    DEFAULT_SEED,
    ITEMS,
    LB_TO_KG,
    SIM_END,
    SIM_START,
    VENDORS,
    Item,
)

CAFE_ITEMS = {"G07", "K03", "D01", "B03", "P12"}
BEEF_ITEMS = {"M03", "M04"}
# vendor reliability drifts over time (per year): Spice Route gets worse, Peel Fresh improves
ON_TIME_TREND = {"V05": -0.09, "V02": 0.03}
BILLING_ERROR_P = {"V03": 0.10, "V08": 0.0}  # chance a shorted line is billed in full
DEFAULT_BILLING_ERROR_P = 0.03
WASTE_LOG_COMPLIANCE = 0.75
SAFETY_OVERRIDE = {"S05": 1.0, "S06": 1.0, "G07": 1.5}  # owner keeps these lean
UNMAPPED_ALIASES = {"cukes", "jeera"}  # left out of the curated alias file on purpose


# --------------------------------------------------------------------------- demand
def demand_factor(d: date, cafe: bool) -> float:
    wd, m, day = d.weekday(), d.month, d.day
    if cafe:
        f = 1.0 if wd < 5 else 0.0
        if m in (7, 8):
            f *= 0.7
        if (m == 12 and day >= 22) or (m == 1 and day <= 3):
            f *= 0.2
        return f
    f = 1.0 if wd < 5 else 0.10
    if m in (7, 8):
        f *= 0.45                      # schools out
    if m == 12 and day <= 19:
        f *= 1.35                      # holiday staff parties
    if (m == 12 and day >= 22) or (m == 1 and day <= 3):
        f *= 0.20                      # winter break
    if m == 3 and 11 <= day <= 19:
        f *= 0.55                      # March break
    if m == 6 and day <= 26:
        f *= 1.15                      # year-end events
    if m == 9 and day <= 8:
        f *= 0.80                      # September ramp-up
    years = (d - SIM_START).days / 365.25
    return f * (1 + 0.04 * years)      # modest growth


# --------------------------------------------------------------------------- prices
def build_market_prices(seed: int, months: list[pd.Period],
                        real_index: pd.DataFrame | None = None) -> tuple[dict, dict]:
    """Monthly market benchmark per item, in CAD per base unit.

    Simulated mode: category inflation profile + noise + seasonality.
    Real mode: the item's Jan-2023 wholesale price x a real StatCan index
    (Ontario average retail price or CPI), so the *movement* is real.
    """
    real = {}
    if real_index is not None and len(real_index):
        for r in real_index.itertuples():
            real[(r.item_id, pd.Period(r.month, "M"))] = (r.index_value, r.source)
    out, source = {}, {}
    for idx, it in enumerate(ITEMS):
        rng = np.random.default_rng([seed, 1000 + idx])  # per-item stream: stable when months are appended
        level, last_real = 1.0, None
        for i, mo in enumerate(months):
            noise = rng.normal(0, it.vol * 0.25)  # always drawn, so both modes consume the stream identically
            if (it.item_id, mo) in real:
                last_real = real[(it.item_id, mo)]
                out[(it.item_id, mo)] = round(it.base_price * last_real[0], 4)
                source[(it.item_id, mo)] = last_real[1]
                continue
            if last_real is not None:  # real series ended (publication lag): carry the last real level forward
                out[(it.item_id, mo)] = round(it.base_price * last_real[0], 4)
                source[(it.item_id, mo)] = last_real[1] + " (carried forward)"
                continue
            if i > 0:
                rate = CATEGORY_INFLATION[it.category].get(mo.year, 0.025)
                if it.item_id in BEEF_ITEMS and mo.year == 2025:
                    rate += BEEF_EXTRA_2025
                level *= 1 + rate / 12 + noise
            seasonal = 1 + it.seasonal_amp * math.cos(2 * math.pi * (mo.month - 2) / 12)
            if it.category == "Dairy" and mo.month == 2 and i > 0:
                level *= 1.012  # supply-managed dairy: small step each February
            out[(it.item_id, mo)] = round(it.base_price * level * seasonal, 4)
            source[(it.item_id, mo)] = "simulated"
    return out, source


# --------------------------------------------------------------------------- core sim
def simulate(start: date = SIM_START, end: date = SIM_END, seed: int = DEFAULT_SEED,
             real_index: pd.DataFrame | None = None, scale: float = 1.0) -> dict[str, pd.DataFrame]:
    # scale multiplies every item's daily demand: 1.0 = the original ~$335k/yr kitchen,
    # 0.25 ≈ a sub-$100k/yr operation. RNG draws don't depend on it, so scale=1.0 is byte-identical.
    items = ITEMS if scale == 1.0 else [replace(it, daily_use=it.daily_use * scale) for it in ITEMS]
    rng = np.random.default_rng(seed)
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    horizon = days + [end + timedelta(n) for n in range(1, 30)]
    months = sorted({pd.Period(d, "M") for d in horizon})
    market, market_source = build_market_prices(seed, months, real_index)
    factor = {(d, c): demand_factor(d, c) for d in horizon for c in (True, False)}

    # owner habits: some items are chronically over-ordered (-> waste)
    over_bias = {it.item_id: rng.uniform(1.0, 1.15) for it in items}
    for iid in ("P01", "P06", "P10", "B03", "M06"):
        over_bias[iid] = rng.uniform(1.35, 1.6)
    safety_days = {
        "Produce": 1.0, "Protein": 1.0, "Dairy": 2.0, "Bakery": 0.5,
        "Dry Goods": 4.0, "Spices": 10.0, "Packaging": 6.0,
    }
    trim_rate = {"Produce": 0.06, "Protein": 0.03, "Bakery": 0.07, "Dairy": 0.02}

    on_hand = {it.item_id: it.daily_use * 4 for it in items}
    pipeline_orders: dict[date, list[dict]] = defaultdict(list)  # arrival date -> lines
    on_order = defaultdict(float)
    last_emergency: dict[str, date] = {}
    week_price: dict[tuple[str, str, str], float] = {}

    po_rows, rec_rows, inv_rows, count_rows, waste_rows = [], [], [], [], []
    truth_rows = []
    po_seq = defaultdict(int)
    inv_seq = defaultdict(lambda: 10000)

    def vendor_price(vid: str, it: Item, d: date) -> float:
        wk = f"{d.isocalendar()[0]}-{d.isocalendar()[1]}"
        key = (vid, it.item_id, wk)
        if key not in week_price:
            v = VENDORS[vid]
            mkt = market[(it.item_id, pd.Period(d, "M"))]
            week_price[key] = mkt * v.markup * (1 + rng.normal(0, v.price_noise))
        return week_price[key]

    def place_order(vid: str, d: date, lines: list[tuple[Item, float]], walk_in: bool = False):
        v = VENDORS[vid]
        po_seq[vid] += 1
        po_no = f"{'WI' if walk_in else 'PO'}-{vid}-{d:%y%m%d}-{po_seq[vid]:03d}"
        promised = d + timedelta(int(round(v.lead_days)))
        years = (d - start).days / 365.25
        on_time_p = min(0.99, max(0.4, v.on_time_p + ON_TIME_TREND.get(vid, 0) * years))
        late = (not walk_in) and rng.random() > on_time_p
        delay = int(rng.choice([1, 1, 1, 2, 2, 3, 5])) if late else 0
        if not walk_in and not late and v.lead_days > 1 and rng.random() < 0.08:
            delay = -1  # occasionally early
        arrival = promised + timedelta(delay)
        if walk_in:
            arrival = d
        for it, units in lines:
            sku = sku_for(vid, it)
            uom = invoice_uom(vid, it)
            ppu = units_per_uom(vid, it)
            if not walk_in:
                po_rows.append(dict(po_number=po_no, vendor_id=vid, order_date=d, expected_date=promised,
                                    vendor_sku=sku, qty_ordered=units, uom=uom))
            if walk_in or rng.random() < v.fill_p:
                received = units
            elif units == 1 or rng.random() < 0.15:
                received = 0                     # out of stock on that line
            else:
                received = min(units - 1, max(1, math.floor(units * rng.uniform(0.4, 0.95))))  # a real short
            on_order[it.item_id] += units * ppu
            pipeline_orders[arrival].append(dict(po=po_no, vid=vid, item=it, units=units, received=received,
                                                 ppu=ppu, sku=sku, uom=uom, walk_in=walk_in))

    for d in days:
        # ---- 1. deliveries arrive, get received, get invoiced
        todays = pipeline_orders.pop(d, [])
        by_po = defaultdict(list)
        for ln in todays:
            by_po[ln["po"]].append(ln)
        for po_no, lines in by_po.items():
            vid = lines[0]["vid"]
            inv_seq[vid] += 1
            invoice_no = f"{vid}-INV-{inv_seq[vid]}"
            for ln in lines:
                it = ln["item"]
                on_order[it.item_id] -= ln["units"] * ln["ppu"]
                on_hand[it.item_id] += ln["received"] * ln["ppu"]
                if not ln["walk_in"]:
                    rec_rows.append(dict(po_number=po_no, vendor_sku=ln["sku"], received_date=d,
                                         qty_received=ln["received"], received_by=rng.choice(["MK", "RS", "AJ", "kitchen"]),
                                         notes="SHORT" if ln["received"] < ln["units"] else ""))
                billed = ln["received"]
                overbilled = False
                if billed < ln["units"] and rng.random() < BILLING_ERROR_P.get(vid, DEFAULT_BILLING_ERROR_P):
                    billed = ln["units"]  # billed for what was ordered, not what showed up
                    overbilled = True
                if billed == 0:
                    continue
                price = vendor_price(vid, it, d) * ln["ppu"]
                inv_rows.append(dict(invoice_number=invoice_no, vendor_id=vid, invoice_date=d, po_ref=po_no,
                                     vendor_sku=ln["sku"], description=desc_for(vid, it), qty=billed,
                                     uom=ln["uom"], unit_price=round(price, 2),
                                     line_total=round(price * billed, 2), line_type="INV", _overbilled=overbilled))
            # occasional credit memo for damaged goods
            if rng.random() < 0.02 and lines:
                ln = lines[0]
                price = vendor_price(vid, ln["item"], d) * ln["ppu"]
                inv_rows.append(dict(invoice_number=f"{vid}-CR-{inv_seq[vid]}", vendor_id=vid, invoice_date=min(d + timedelta(3), end),
                                     po_ref=po_no, vendor_sku=ln["sku"], description=desc_for(vid, ln["item"]),
                                     qty=-1, uom=ln["uom"], unit_price=round(price, 2),
                                     line_total=round(-price, 2), line_type="CR", _overbilled=False))

        # ---- 2. consumption, stockouts, waste
        for it in items:
            f = factor[(d, it.item_id in CAFE_ITEMS)]
            demand = it.daily_use * f * rng.lognormal(0, 0.18) if f > 0 else 0.0
            used = min(demand, on_hand[it.item_id])
            unmet = demand - used
            on_hand[it.item_id] -= used
            waste = 0.0
            tr = trim_rate.get(it.category, 0.0)
            waste += used * tr * rng.uniform(0.5, 1.5)
            if it.shelf_life < 60:
                max_fresh = it.daily_use * it.shelf_life
                if on_hand[it.item_id] > max_fresh:
                    waste += (on_hand[it.item_id] - max_fresh) * 0.25
            waste = min(waste, on_hand[it.item_id])
            on_hand[it.item_id] -= waste
            truth_rows.append(dict(date=d, item_id=it.item_id, true_usage=used, true_waste=waste,
                                   unmet_demand=unmet, on_hand_eod=on_hand[it.item_id]))
            if waste > 0.05 * it.daily_use and rng.random() < WASTE_LOG_COMPLIANCE:
                waste_rows.append(dict(date=d, item=rng.choice(it.count_names or (it.name,)),
                                       qty=round(waste, 2), uom=it.base_uom.lower(),
                                       reason=rng.choice(["spoiled", "expired", "trim", "dropped", "overprep"],
                                                         p=[0.35, 0.25, 0.25, 0.05, 0.10])))
            # emergency walk-in buy after a stockout
            if unmet > 0.2 * it.daily_use and f > 0.3:
                last = last_emergency.get(it.item_id)
                if last is None or (d - last).days > 2:
                    ppu = units_per_uom(BACKUP_VENDOR, it)
                    units = max(1, math.ceil(it.daily_use * 2 / ppu))
                    pipeline_orders[d + timedelta(1)].append(dict(
                        po=f"WI-{BACKUP_VENDOR}-{d:%y%m%d}-{it.item_id}", vid=BACKUP_VENDOR, item=it,
                        units=units, received=units, ppu=ppu, sku=sku_for(BACKUP_VENDOR, it),
                        uom=invoice_uom(BACKUP_VENDOR, it), walk_in=True))
                    on_order[it.item_id] += units * ppu
                    last_emergency[it.item_id] = d

        # ---- 3. periodic-review ordering, per vendor order day
        for vid, v in VENDORS.items():
            if d.weekday() not in v.order_weekdays:
                continue
            next_gap = min(((wd - d.weekday()) % 7) or 7 for wd in v.order_weekdays)
            cover = next_gap + int(round(v.lead_days)) + 1
            lines = []
            for it in (i for i in items if i.vendor_id == vid):
                cafe = it.item_id in CAFE_ITEMS
                expected = sum(it.daily_use * factor[(d + timedelta(k), cafe)] for k in range(1, cover + 1))
                sd = SAFETY_OVERRIDE.get(it.item_id, safety_days[it.category])
                target = expected * over_bias[it.item_id] + it.daily_use * sd
                position = on_hand[it.item_id] + on_order[it.item_id]
                need = target - position
                if need <= 0:
                    continue
                ppu = units_per_uom(vid, it)
                units = math.ceil(need / ppu)
                lines.append((it, units))
            if lines:
                place_order(vid, d, lines)

        # ---- 4. weekly hand count (Saturday)
        if d.weekday() == 5:
            for it in items:
                if rng.random() < 0.03:
                    continue  # skipped this week
                true_qty = on_hand[it.item_id] * rng.normal(1, 0.03)
                name = rng.choice((it.name,) + it.count_names)
                uom, qty = it.base_uom.lower(), true_qty
                r = rng.random()
                if it.vendor_id == "V03" and r < 0.35:
                    uom, qty = "lb", true_qty / LB_TO_KG
                elif r < 0.15 and it.pack_size > 1:
                    uom, qty = "cs" if invoice_uom(it.vendor_id, it) == "CS" else invoice_uom(it.vendor_id, it).lower(), true_qty / it.pack_size
                qty = round(qty, 1) if uom not in ("ea",) else round(qty)
                count_rows.append(dict(count_date=d, item=name, qty=qty, uom=uom,
                                       counted_by=rng.choice(["MK", "RS", "AJ"])))

    return dict(
        purchase_orders=pd.DataFrame(po_rows),
        receiving=pd.DataFrame(rec_rows),
        invoices=pd.DataFrame(inv_rows),
        counts=pd.DataFrame(count_rows),
        waste=pd.DataFrame(waste_rows),
        truth=pd.DataFrame(truth_rows),
        market=pd.DataFrame(
            [dict(month=str(mo), item_id=iid, benchmark_price=p, source=market_source[(iid, mo)])
             for (iid, mo), p in market.items() if mo <= pd.Period(end, "M")]
        ),
    )


# --------------------------------------------------------------------------- catalog helpers
def invoice_uom(vid: str, it: Item) -> str:
    if vid == BACKUP_VENDOR:
        return it.base_uom.upper() if it.base_uom != "ea" else "EA"
    return it.vendor_uom


def units_per_uom(vid: str, it: Item) -> float:
    if vid == BACKUP_VENDOR:
        return 1.0
    return LB_TO_KG if it.vendor_uom == "LB" else it.pack_size


def sku_for(vid: str, it: Item) -> str:
    prefix = {"V01": "NF", "V02": "PF", "V03": "LM", "V04": "CV", "V05": "SR", "V06": "GP", "V07": "HB", "V08": "BC"}[vid]
    return f"{prefix}-{it.item_id[0]}{int(it.item_id[1:]) * 37 + 1000}"


def desc_for(vid: str, it: Item) -> str:
    base = it.name.upper() if vid in ("V01", "V03") else it.name
    if vid == "V08":
        base = f"{it.name} (retail)"
    return base


def catalog() -> pd.DataFrame:
    rows = []
    for it in ITEMS:
        for vid in (it.vendor_id, BACKUP_VENDOR):
            rows.append(dict(vendor_id=vid, vendor_sku=sku_for(vid, it), vendor_description=desc_for(vid, it),
                             item_id=it.item_id, invoice_uom=invoice_uom(vid, it),
                             base_units_per_uom=round(units_per_uom(vid, it), 5)))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- mess + export
DATE_FORMATS = ["%Y-%m-%d", "%Y-%m-%d", "%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%b %d %Y"]
UOM_VARIANTS = {"LB": ["LB", "lb", "lbs", "LBS"], "CS": ["CS", "cs", "case", "Case"], "KG": ["KG", "kg", "Kg"],
                "BAG": ["BAG", "bag", "Bag"], "EA": ["EA", "ea", "each"], "DZ": ["DZ", "dz", "dozen"],
                "JUG": ["JUG", "jug"], "L": ["L", "l", "LTR"]}


def _month_rng(seed: int, salt: int, month: str) -> np.random.Generator:
    return np.random.default_rng([seed, salt, int(month.replace("_", ""))])


def make_messy(sim: dict[str, pd.DataFrame], seed: int) -> dict[str, pd.DataFrame]:
    """Inject realistic export problems. Each month uses its own RNG stream, so extending the
    simulation never changes earlier files. Every injection is logged as ground truth."""
    injections = []
    inv_all = sim["invoices"].copy()
    inv_all["_file_month"] = pd.to_datetime(inv_all["invoice_date"]).dt.strftime("%Y_%m")
    text_vendors = {vid for vid, v in VENDORS.items() if v.price_as_text}
    parts = []
    for month, inv in inv_all.groupby("_file_month", sort=True):
        rng = _month_rng(seed, 1, month)
        inv = inv.copy()
        n = len(inv)
        inv["vendor_name"] = [rng.choice(VENDORS[v].aliases) for v in inv["vendor_id"]]
        inv["invoice_date"] = [d.strftime(rng.choice(DATE_FORMATS)) for d in inv["invoice_date"]]
        inv["uom"] = [rng.choice(UOM_VARIANTS.get(u, [u])) for u in inv["uom"]]
        inv["unit_price"] = inv["unit_price"].astype(object)
        inv["line_total"] = inv["line_total"].astype(object)
        typo = rng.random(n) < 0.002
        inv.loc[typo, "unit_price"] = inv.loc[typo, "unit_price"].astype(float) * 10
        missing = (rng.random(n) < 0.006) & ~typo
        inv.loc[missing, "unit_price"] = None
        neg = (rng.random(n) < 0.0015) & (inv["line_type"] == "INV").to_numpy() & ~typo
        inv.loc[neg, "qty"] = -inv.loc[neg, "qty"]
        for kind, mask in [("price_decimal_typo", typo), ("missing_price", missing), ("negative_qty_typo", neg)]:
            for r in inv.loc[mask].itertuples():
                injections.append(dict(kind=kind, table="invoices", key=f"{r.invoice_number}|{r.vendor_sku}"))
        mask = inv["vendor_id"].isin(text_vendors) & inv["unit_price"].notna()
        inv.loc[mask, "unit_price"] = inv.loc[mask, "unit_price"].map(lambda x: f"${float(x):,.2f}")
        inv.loc[mask, "line_total"] = inv.loc[mask, "line_total"].map(lambda x: f"${float(x):,.2f}")
        dup_mask = rng.random(n) < 0.015
        dupes = inv.loc[dup_mask]
        for r in dupes.itertuples():
            injections.append(dict(kind="duplicate_line", table="invoices", key=f"{r.invoice_number}|{r.vendor_sku}"))
        parts.append(pd.concat([inv, dupes]).sort_index(kind="stable"))
    inv = pd.concat(parts).reset_index(drop=True).drop(columns=["vendor_id", "_overbilled"])

    po = sim["purchase_orders"].copy()
    po_month = pd.to_datetime(po["order_date"]).dt.strftime("%Y_%m")
    po["uom"] = [None] * len(po)
    for month, idx in po.groupby(po_month).groups.items():
        rng = _month_rng(seed, 2, month)
        po.loc[idx, "uom"] = [rng.choice(UOM_VARIANTS.get(u, [u])) for u in sim["purchase_orders"].loc[idx, "uom"]]

    counts = sim["counts"].copy()
    counts["qty"] = counts["qty"].astype(object)
    c_month = pd.to_datetime(counts["count_date"]).dt.strftime("%Y_%m")
    for month, idx in counts.groupby(c_month).groups.items():
        rng = _month_rng(seed, 3, month)
        blank = rng.random(len(idx)) < 0.01
        shout = rng.random(len(idx)) < 0.2
        blank_idx = idx[blank]
        counts.loc[blank_idx, "qty"] = None
        counts.loc[idx[shout], "item"] = counts.loc[idx[shout], "item"].map(lambda x: f"  {x.upper()} ")
        for k in blank_idx:
            injections.append(dict(kind="blank_count", table="inventory_counts",
                                   key=f"{counts.at[k, 'count_date']}|{counts.at[k, 'item']}"))
    for r in counts.itertuples():
        if r.item.strip().lower() in UNMAPPED_ALIASES:
            injections.append(dict(kind="unmapped_alias", table="inventory_counts", key=f"{r.count_date}|{r.item}"))

    truth_inv = sim["invoices"][["invoice_number", "vendor_id", "invoice_date", "po_ref", "vendor_sku", "qty",
                                 "unit_price", "line_total", "line_type", "_overbilled"]]
    return dict(purchase_orders=po, receiving=sim["receiving"], invoices=inv, counts=counts,
                waste=sim["waste"], market=sim["market"], truth=sim["truth"],
                truth_invoices=truth_inv, injections=pd.DataFrame(injections))


def write_raw(out_dir: Path, messy: dict[str, pd.DataFrame]) -> None:
    out_dir = Path(out_dir)
    for sub in ["reference", "purchase_orders", "receiving", "invoices", "inventory_counts", "waste", "market", "_truth"]:
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    # reference data
    pd.DataFrame([dict(vendor_id=v.vendor_id, vendor_name=v.name, payment_terms=t, primary_categories=c)
                  for v, t, c in zip(VENDORS.values(),
                                     ["Net 30", "Net 14", "Net 14", "Net 30", "Net 45", "Net 30", "COD", "Cash"],
                                     ["Dry goods", "Produce", "Protein", "Dairy & eggs", "Spices & legumes",
                                      "Packaging", "Bakery", "Emergency / walk-in"])]
                 ).to_csv(out_dir / "reference/vendor_master.csv", index=False)
    pd.DataFrame([dict(item_id=i.item_id, item_name=i.name, category=i.category, base_uom=i.base_uom,
                       shelf_life_days=i.shelf_life, primary_vendor_id=i.vendor_id) for i in ITEMS]
                 ).to_csv(out_dir / "reference/item_master.csv", index=False)
    catalog().to_csv(out_dir / "reference/vendor_catalog.csv", index=False)
    aliases = [dict(alias=a, item_id=i.item_id) for i in ITEMS for a in (i.name,) + i.count_names
               if a not in UNMAPPED_ALIASES]
    pd.DataFrame(aliases).to_csv(out_dir / "reference/item_aliases.csv", index=False)

    # monthly-partitioned operational exports
    def by_month(df: pd.DataFrame, date_col: str):
        months = pd.to_datetime(df[date_col]).dt.strftime("%Y_%m")
        yield from df.groupby(months)

    for m, part in by_month(messy["purchase_orders"], "order_date"):
        part.to_csv(out_dir / f"purchase_orders/po_{m}.csv", index=False)
    rec = messy["receiving"].copy()
    for m, part in by_month(rec, "received_date"):
        with open(out_dir / f"receiving/receiving_{m}.jsonl", "w") as fh:
            for r in part.to_dict("records"):
                r["received_date"] = r["received_date"].isoformat()
                fh.write(json.dumps(r) + "\n")
    inv = messy["invoices"]
    # partition by the real month (file name), even though the date column inside is messy
    for m, part in inv.groupby("_file_month", sort=True):
        part.drop(columns="_file_month").to_csv(out_dir / f"invoices/invoices_{m}.csv", index=False)
    for m, part in by_month(messy["counts"], "count_date"):
        part.to_csv(out_dir / f"inventory_counts/counts_{m}.csv", index=False)
    messy["waste"].to_csv(out_dir / "waste/waste_log.csv", index=False)
    messy["market"].to_csv(out_dir / "market/market_benchmark_monthly.csv", index=False)
    # ground truth, used only by tests to check the pipeline's reconstruction
    messy["truth"].to_parquet(out_dir / "_truth/daily_truth.parquet", index=False)
    messy["truth_invoices"].to_parquet(out_dir / "_truth/invoices_clean.parquet", index=False)
    messy["injections"].to_csv(out_dir / "_truth/injections.csv", index=False)


def parse_end(value: str) -> date:
    """'today' = yesterday's close of business (the daily job lands yesterday's activity)."""
    return date.today() - timedelta(days=1) if value == "today" else date.fromisoformat(value)


def generate(out_dir: str | Path, start: date = SIM_START, end: date = SIM_END, seed: int = DEFAULT_SEED,
             real_index: pd.DataFrame | None = None, scale: float = 1.0) -> dict:
    sim = simulate(start, end, seed, real_index, scale)
    messy = make_messy(sim, seed)
    write_raw(Path(out_dir), messy)
    return {k: len(v) for k, v in messy.items()}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Generate simulated raw vendor + inventory exports")
    p.add_argument("--out", default="data/raw")
    p.add_argument("--start", default=SIM_START.isoformat())
    p.add_argument("--end", default=SIM_END.isoformat(), help="YYYY-MM-DD or 'today'")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--scale", type=float, default=1.0, help="demand multiplier: 1.0 = ~$335k/yr kitchen, 0.25 = sub-$100k/yr")
    a = p.parse_args(argv)
    stats = generate(a.out, date.fromisoformat(a.start), parse_end(a.end), a.seed, scale=a.scale)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
