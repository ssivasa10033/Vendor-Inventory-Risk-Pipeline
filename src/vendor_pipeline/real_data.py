"""Real market prices from Statistics Canada (open data, no API key).

  * 18-10-0245-01  Monthly average retail prices for selected products (scanner data, 2017+)
  * 18-10-0004-01  Consumer Price Index, monthly (fallback for items with no average-price series)

How it's used: retail shelf prices are not what a caterer pays a distributor, so we don't copy
the *level*. We take the real *movement*: each item's benchmark = its Jan-2023 wholesale price x
(real price this month / real price in Jan 2023). Every row records which StatCan series it came
from, so the dashboard can show "price vs market (source: StatCan 18-10-0245-01, Ontario)".

    vip-fetch-market --out data/external          # downloads, caches, writes statcan_market_index.csv
"""

from __future__ import annotations

import argparse
import io
import time
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

from .config import ITEMS

URL = "https://www150.statcan.gc.ca/n1/tbl/csv/{pid}-eng.zip"
AVG_PRICES, CPI = "18100245", "18100004"
BASE_MONTH = "2023-01"
INDEX_FILE = "statcan_market_index.csv"

# item -> (average-price product regexes, exclusions, CPI fallback regexes in priority order)
ITEM_MATCH: dict[str, tuple[list[str], list[str], list[str]]] = {
    "P01": ([r"^romaine"], [], [r"^fresh vegetables$"]),
    "P02": ([r"^tomatoes"], [r"cann", r"cherry", r"grape"], [r"^fresh vegetables$"]),
    "P03": ([r"^onions"], [r"green"], [r"^fresh vegetables$"]),
    "P04": ([r"^potatoes"], [r"sweet", r"chips", r"frozen"], [r"^potatoes$", r"^fresh vegetables$"]),
    "P05": ([r"^carrots"], [r"baby"], [r"^fresh vegetables$"]),
    "P06": ([r"^peppers"], [], [r"^fresh vegetables$"]),
    "P07": ([r"^cucumber"], [], [r"^fresh vegetables$"]),
    "P08": ([r"^garlic"], [], [r"^fresh vegetables$"]),
    "P09": ([r"^lemons"], [], [r"^fresh fruit$"]),
    "P10": ([r"^cilantro"], [], [r"^fresh vegetables$"]),
    "P11": ([r"^apples"], [r"juice", r"sauce"], [r"^apples$", r"^fresh fruit$"]),
    "P12": ([r"^bananas"], [], [r"^fresh fruit$"]),
    "M01": ([r"^chicken breast"], [r"frozen", r"breaded"], [r"^fresh or frozen chicken$", r"^fresh or frozen poultry$"]),
    "M02": ([r"^chicken thigh", r"^chicken drumstick"], [r"frozen"], [r"^fresh or frozen chicken$", r"^fresh or frozen poultry$"]),
    "M03": ([r"^ground beef"], [r"frozen"], [r"^fresh or frozen beef$"]),
    "M04": ([r"striploin", r"sirloin"], [], [r"^fresh or frozen beef$"]),
    "M05": ([r"^pork shoulder", r"^pork loin", r"^pork rib"], [], [r"^fresh or frozen pork$"]),
    "M06": ([r"^salmon"], [r"canned", r"smoked"], [r"^fresh or frozen fish", r"^fish$"]),
    "D01": ([r"^milk, 4 lit", r"^milk"], [r"plant", r"almond", r"oat", r"soy", r"chocolate", r"evaporated"], [r"^fresh milk$", r"^dairy products$"]),
    "D02": ([r"^butter"], [r"peanut"], [r"^butter$", r"^dairy products$"]),
    "D03": ([r"cheddar", r"^block cheese"], [], [r"^cheese$", r"^dairy products$"]),
    "D04": ([r"shredded cheese", r"mozzarella"], [], [r"^cheese$", r"^dairy products$"]),
    "D05": ([r"cream"], [r"ice cream", r"sour", r"cheese"], [r"^dairy products$"]),
    "D06": ([r"^yogh?urt"], [r"plant"], [r"^dairy products$"]),
    "D07": ([r"^eggs"], [], [r"^eggs$"]),
    "G01": ([r"rice"], [r"crisp", r"cake", r"noodle"], [r"^rice", r"^food purchased from stores$"]),
    "G02": ([r"flour"], [], [r"^flour", r"^food purchased from stores$"]),
    "G03": ([r"canola oil", r"vegetable oil"], [], [r"^edible fats and oils$", r"^food purchased from stores$"]),
    "G04": ([r"pasta"], [r"sauce"], [r"^pasta", r"^food purchased from stores$"]),
    "G05": ([r"^white sugar", r"sugar"], [r"free"], [r"^sugar", r"^food purchased from stores$"]),
    "G06": ([r"canned tomato", r"tomatoes, canned"], [], [r"^food purchased from stores$"]),
    "G07": ([r"coffee"], [r"instant", r"pods"], [r"^coffee$", r"^coffee and tea$", r"^food purchased from stores$"]),
    "S01": ([], [], [r"^food purchased from stores$"]),
    "S02": ([], [], [r"^food purchased from stores$"]),
    "S03": ([], [], [r"^food purchased from stores$"]),
    "S04": ([], [], [r"^food purchased from stores$"]),
    "S05": ([r"lentil"], [], [r"^food purchased from stores$"]),
    "S06": ([r"chickpea"], [], [r"^food purchased from stores$"]),
    "B01": ([r"^white bread", r"bread"], [r"crumbs"], [r"^bread, rolls and buns$", r"^bakery products$"]),
    "B02": ([r"rolls", r"buns"], [r"cinnamon"], [r"^bread, rolls and buns$", r"^bakery products$"]),
    "B03": ([r"croissant"], [], [r"^bakery products$"]),
    "K01": ([], [], [r"^all-items$"]),
    "K02": ([], [], [r"^all-items$"]),
    "K03": ([], [], [r"^all-items$"]),
    "K04": ([], [], [r"^all-items$"]),
}


# --------------------------------------------------------------------------- download + parse
def download_table(pid: str, cache_dir: Path, max_age_days: float = 7) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    zpath = cache_dir / f"{pid}-eng.zip"
    if not zpath.exists() or (time.time() - zpath.stat().st_mtime) > max_age_days * 86400:
        req = urllib.request.Request(URL.format(pid=pid), headers={"User-Agent": "vendor-pipeline/0.1 (portfolio project)"})
        with urllib.request.urlopen(req, timeout=180) as r:
            zpath.write_bytes(r.read())
    return zpath


def read_statcan_csv(source, geo: str, since: str = "2022-01") -> pd.DataFrame:
    """Read a StatCan full-table CSV (path, zip, or file-like) -> month, product, value.
    The product dimension column name differs by table, so find it: it sits between DGUID and UOM."""
    is_zip = (str(source).endswith(".zip") if isinstance(source, str | Path)
              else zipfile.is_zipfile(source))
    if hasattr(source, "seek"):
        source.seek(0)
    if is_zip:
        with zipfile.ZipFile(source) as z:
            name = next(n for n in z.namelist() if n.endswith(".csv") and "MetaData" not in n)
            return read_statcan_csv(io.BytesIO(z.read(name)), geo, since)
    header = pd.read_csv(source, nrows=0, encoding="utf-8-sig").columns.tolist()
    if hasattr(source, "seek"):
        source.seek(0)
    dim = header[header.index("DGUID") + 1]
    frames = []
    for chunk in pd.read_csv(source, usecols=["REF_DATE", "GEO", dim, "VALUE"], dtype=str,
                             chunksize=500_000, encoding="utf-8-sig"):
        chunk = chunk[(chunk["GEO"] == geo) & (chunk["REF_DATE"] >= since)]
        frames.append(chunk)
    df = pd.concat(frames, ignore_index=True).rename(columns={"REF_DATE": "month", dim: "product", "VALUE": "value"})
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["value"])[["month", "product", "value"]]


# --------------------------------------------------------------------------- mapping
def _pick(series: pd.DataFrame, patterns: list[str], exclude: list[str]) -> str | None:
    products = series["product"].drop_duplicates()
    low = products.str.lower()
    for pat in patterns:  # patterns are in priority order
        ok = low.str.contains(pat, regex=True)
        for ex in exclude:
            ok &= ~low.str.contains(ex, regex=True)
        cands = products[ok]
        # must have the base month, then prefer longest coverage, then shortest (most generic) name
        cands = [p for p in cands if (series.loc[series["product"] == p, "month"] == BASE_MONTH).any()]
        if cands:
            cov = {p: (series["product"] == p).sum() for p in cands}
            return sorted(cands, key=lambda p: (-cov[p], len(p)))[0]
    return None


def build_index(avg: pd.DataFrame, cpi: pd.DataFrame | None, geo: str) -> pd.DataFrame:
    rows = []
    for it in ITEMS:
        pats, excl, cpi_pats = ITEM_MATCH.get(it.item_id, ([], [], []))
        chosen, table, label = None, None, None
        if avg is not None and len(avg):
            chosen = _pick(avg, pats, excl)
            table, label = (avg, f"StatCan 18-10-0245-01 avg price: {chosen} ({geo})") if chosen else (None, None)
        if chosen is None and cpi is not None and len(cpi):
            chosen = _pick(cpi, cpi_pats, [])
            table, label = (cpi, f"StatCan CPI 18-10-0004-01: {chosen} ({geo})") if chosen else (None, None)
        if chosen is None:
            continue  # stays simulated
        s = table[table["product"] == chosen].groupby("month")["value"].mean().sort_index()
        base = s.loc[BASE_MONTH]
        for month, v in s.items():
            if month >= BASE_MONTH:
                rows.append(dict(item_id=it.item_id, month=month, index_value=round(v / base, 5),
                                 real_value=v, source=label))
    return pd.DataFrame(rows, columns=["item_id", "month", "index_value", "real_value", "source"])


def fetch_real_index(out_dir: str | Path = "data/external", geo: str = "Ontario", max_age_days: float = 7) -> pd.DataFrame:
    out_dir = Path(out_dir)
    avg = read_statcan_csv(download_table(AVG_PRICES, out_dir, max_age_days), geo)
    try:
        cpi = read_statcan_csv(download_table(CPI, out_dir, max_age_days), geo)
    except Exception as e:  # CPI is only a fallback
        print(f"[real_data] CPI fallback unavailable: {e}")
        cpi = None
    idx = build_index(avg, cpi, geo)
    idx.to_csv(out_dir / INDEX_FILE, index=False)
    return idx


def load_or_fetch(out_dir: str | Path = "data/external", mode: str = "auto") -> pd.DataFrame | None:
    """mode: 'real' (must succeed), 'simulated' (never fetch), 'auto' (try, fall back)."""
    if mode == "simulated":
        return None
    out_dir = Path(out_dir)
    try:
        idx = fetch_real_index(out_dir)
        print(f"[real_data] StatCan index: {idx['item_id'].nunique()} items, through {idx['month'].max()}")
        return idx
    except Exception as e:
        cached = out_dir / INDEX_FILE
        if cached.exists():
            print(f"[real_data] download failed ({e}); using cached {cached}")
            return pd.read_csv(cached)
        if mode == "real":
            raise
        print(f"[real_data] StatCan unreachable ({e}); falling back to simulated market prices")
        return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Download real StatCan food prices and build the market index")
    p.add_argument("--out", default="data/external")
    p.add_argument("--geo", default="Ontario")
    a = p.parse_args(argv)
    idx = fetch_real_index(a.out, a.geo, max_age_days=0)
    summary = idx.groupby("item_id").agg(source=("source", "first"), months=("month", "nunique"),
                                         latest=("month", "max"), change=("index_value", "last"))
    print(summary.to_string())


if __name__ == "__main__":
    main()
