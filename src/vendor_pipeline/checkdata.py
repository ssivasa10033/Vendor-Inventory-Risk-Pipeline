"""vip-check-data: validate YOUR raw exports against the pipeline's contract, before running it.

Checks structure (files + columns), parseability (dates, money), and cross-file consistency
(do invoice SKUs exist in the catalog? do count-sheet names map to items?). FAIL means the
pipeline cannot run or would quarantine almost everything; WARN means it will run but some
rows will land in quarantine. Schema guide: docs/use-your-own-data.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from .silver import DATE_FORMATS
from .storage import LocalStorage

# pattern -> (reader, required columns, date columns, numeric columns)
CONTRACT: dict[str, tuple[str, list[str], list[str], list[str]]] = {
    "reference/vendor_master.csv": ("csv", ["vendor_id", "vendor_name", "payment_terms", "primary_categories"], [], []),
    "reference/item_master.csv": ("csv", ["item_id", "item_name", "category", "base_uom", "shelf_life_days",
                                          "primary_vendor_id"], [], ["shelf_life_days"]),
    "reference/vendor_catalog.csv": ("csv", ["vendor_id", "vendor_sku", "vendor_description", "item_id",
                                             "invoice_uom", "base_units_per_uom"], [], ["base_units_per_uom"]),
    "reference/item_aliases.csv": ("csv", ["alias", "item_id"], [], []),
    "purchase_orders/*.csv": ("csv", ["po_number", "vendor_id", "order_date", "expected_date", "vendor_sku",
                                      "qty_ordered", "uom"], ["order_date", "expected_date"], ["qty_ordered"]),
    "receiving/*.jsonl": ("jsonl", ["po_number", "vendor_sku", "received_date", "qty_received"],
                          ["received_date"], ["qty_received"]),
    "invoices/*.csv": ("csv", ["invoice_number", "invoice_date", "po_ref", "vendor_sku", "description", "qty",
                               "uom", "unit_price", "line_total", "line_type", "vendor_name"],
                       ["invoice_date"], ["qty", "line_total"]),
    "inventory_counts/*.csv": ("csv", ["count_date", "item", "qty", "uom", "counted_by"], ["count_date"], ["qty"]),
    "waste/*.csv": ("csv", ["date", "item", "qty", "uom", "reason"], ["date"], ["qty"]),
    "market/*.csv": ("csv", ["month", "item_id", "benchmark_price"], [], ["benchmark_price"]),
}


def _date_share(s: pd.Series) -> tuple[float, str]:
    """Share of values parseable by silver's explicit format list, plus one example failure."""
    s = s.dropna().astype(str).str.strip()
    s = s[s != ""].head(1000)
    if s.empty:
        return 0.0, "(column is empty)"
    parsed = pd.Series(pd.NaT, index=s.index)
    for fmt in DATE_FORMATS:
        mask = parsed.isna()
        parsed[mask] = pd.to_datetime(s[mask], format=fmt, errors="coerce")
    bad = s[parsed.isna()]
    return 1 - len(bad) / len(s), (repr(bad.iloc[0]) if len(bad) else "")


def _num_share(s: pd.Series) -> tuple[float, str]:
    """Share of values numeric after stripping $ , and whitespace, plus one example failure."""
    s = s.dropna().astype(str).str.strip()
    s = s[s != ""].head(1000)
    if s.empty:
        return 0.0, "(column is empty)"
    cleaned = s.str.replace(re.compile(r"[$,\s]"), "", regex=True)
    bad = s[pd.to_numeric(cleaned, errors="coerce").isna()]
    return 1 - len(bad) / len(s), (repr(bad.iloc[0]) if len(bad) else "")


def _norm(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)


def check(base: str | Path) -> list[dict]:
    """Run every check. Returns [{source, level: PASS|WARN|FAIL, message}, ...]."""
    out: list[dict] = []
    base = Path(base)

    def add(source: str, level: str, message: str) -> None:
        out.append(dict(source=source, level=level, message=message))

    if not (base / "raw").is_dir():
        add("folder", "FAIL", f"{base} has no raw/ subfolder. Expected <your folder>/raw/<the ten sources>.")
        return out

    st = LocalStorage(base)
    frames: dict[str, pd.DataFrame] = {}
    for pattern, (kind, req, date_cols, num_cols) in CONTRACT.items():
        name = pattern.split("/")[0] if "*" in pattern else pattern.split("/")[-1]
        files = st.raw_files(pattern)
        if not files:
            add(name, "FAIL", f"no files match raw/{pattern}")
            continue
        df = st.read_raw_csv(pattern) if kind == "csv" else st.read_raw_jsonl(pattern)
        if df.empty:
            add(name, "FAIL", f"raw/{pattern}: file(s) exist but contain no rows")
            continue
        missing = [c for c in req if c not in df.columns]
        if missing:
            add(name, "FAIL", f"missing columns {missing} (has {sorted(set(df.columns) - {'_source_file'})})")
            continue
        problems = []
        for c in date_cols:
            share, example = _date_share(df[c])
            if share < 0.95:
                problems.append(f"{c}: only {share:.0%} of values match an accepted date format "
                                f"({', '.join(DATE_FORMATS)}); e.g. {example}")
        for c in num_cols:
            share, example = _num_share(df[c])
            if share < 0.95:
                problems.append(f"{c}: only {share:.0%} of values are numeric; e.g. {example}")
        if problems:
            add(name, "FAIL", "; ".join(problems))
        else:
            add(name, "PASS", f"{len(df):,} rows, {len(files)} file(s), columns and formats OK")
        frames[pattern] = df

    # ---- cross-file consistency: the checks that decide how much lands in quarantine
    inv, cat = frames.get("invoices/*.csv"), frames.get("reference/vendor_catalog.csv")
    if inv is not None and cat is not None:
        hit = inv["vendor_sku"].isin(set(cat["vendor_sku"])).mean()
        add("invoices ↔ catalog", "PASS" if hit >= 0.9 else "WARN",
            f"{hit:.0%} of invoice SKUs found in vendor_catalog" +
            ("" if hit >= 0.9 else " — unmatched lines will be quarantined, add the missing SKUs to the catalog"))
    cnt, ali, im = (frames.get("inventory_counts/*.csv"), frames.get("reference/item_aliases.csv"),
                    frames.get("reference/item_master.csv"))
    if cnt is not None and ali is not None and im is not None:
        known = set(_norm(ali["alias"])) | set(_norm(im["item_name"]))
        hit = _norm(cnt["item"]).isin(known).mean()
        add("counts ↔ aliases", "PASS" if hit >= 0.9 else "WARN",
            f"{hit:.0%} of count-sheet names map to an item" +
            ("" if hit >= 0.9 else " — unmapped counts will be quarantined, add spellings to item_aliases.csv"))
    rec, po = frames.get("receiving/*.jsonl"), frames.get("purchase_orders/*.csv")
    if rec is not None and po is not None:
        hit = rec["po_number"].isin(set(po["po_number"])).mean()
        add("receiving ↔ POs", "PASS" if hit >= 0.9 else "WARN",
            f"{hit:.0%} of receipts reference a known PO" +
            ("" if hit >= 0.9 else " — orphan receipts can't be scored for on-time/in-full"))
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Validate your own raw data against the pipeline contract")
    p.add_argument("--base", default="data", help="folder that contains the raw/ subfolder")
    a = p.parse_args(argv)
    results = check(a.base)
    icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
    for r in results:
        print(f"  {icon[r['level']]} {r['source']}: {r['message']}")
    fails = sum(r["level"] == "FAIL" for r in results)
    warns = sum(r["level"] == "WARN" for r in results)
    print(f"\n{'NOT READY' if fails else 'READY'}: {fails} blocking problem(s), {warns} warning(s)."
          + ("" if fails else f"  Next: vip-run-pipeline --env local --base {a.base}"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
