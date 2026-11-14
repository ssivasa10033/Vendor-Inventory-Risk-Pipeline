"""Bronze = land the raw exports as-is.

No cleaning here on purpose. Every column stays a string, every row is kept,
and we stamp where it came from and when we loaded it. If silver logic is ever
wrong, bronze lets us replay from the original data.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

SOURCES = {
    # table name: (reader, glob pattern)
    "vendor_master": ("csv", "reference/vendor_master.csv"),
    "item_master": ("csv", "reference/item_master.csv"),
    "vendor_catalog": ("csv", "reference/vendor_catalog.csv"),
    "item_aliases": ("csv", "reference/item_aliases.csv"),
    "purchase_orders": ("csv", "purchase_orders/*.csv"),
    "receiving": ("jsonl", "receiving/*.jsonl"),
    "invoices": ("csv", "invoices/*.csv"),
    "inventory_counts": ("csv", "inventory_counts/*.csv"),
    "waste_log": ("csv", "waste/*.csv"),
    "market_benchmark": ("csv", "market/*.csv"),
}


def ingest(storage) -> dict[str, pd.DataFrame]:
    loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = {}
    for name, (kind, pattern) in SOURCES.items():
        df = storage.read_raw_csv(pattern) if kind == "csv" else storage.read_raw_jsonl(pattern)
        if df.empty:
            raise FileNotFoundError(f"No raw files found for {name!r} ({pattern})")
        df["_ingested_at"] = loaded_at
        out[name] = df
        storage.write("bronze", name, df)
    return out
