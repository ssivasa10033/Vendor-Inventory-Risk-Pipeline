"""Where data lives. Same pipeline code, two backends:

* LocalStorage      -> ./data/{raw,bronze,silver,gold} as Parquet (+ CSV for gold)
* DatabricksStorage -> raw files in a Unity Catalog Volume, tables as Delta in
                       <catalog>.<schema> (e.g. workspace.vendor_inventory.gold_vendor_risk)

The pipeline only ever calls `read_raw_*`, `write(layer, name, df)` and `read(layer, name)`.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import pandas as pd


def on_databricks() -> bool:
    return "DATABRICKS_RUNTIME_VERSION" in os.environ


class LocalStorage:
    def __init__(self, base: str | Path = "data"):
        self.base = Path(base)
        self.raw = self.base / "raw"

    # ---- raw (files) -------------------------------------------------------
    def raw_files(self, pattern: str) -> list[str]:
        return sorted(glob.glob(str(self.raw / pattern)))

    def read_raw_csv(self, pattern: str) -> pd.DataFrame:
        frames = []
        for f in self.raw_files(pattern):
            df = pd.read_csv(f, dtype=str, keep_default_na=False, na_values=[""])
            df["_source_file"] = os.path.basename(f)
            frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def read_raw_jsonl(self, pattern: str) -> pd.DataFrame:
        rows = []
        for f in self.raw_files(pattern):
            with open(f) as fh:
                for line in fh:
                    if line.strip():
                        r = {k: (None if v is None else str(v)) for k, v in json.loads(line).items()}
                        r["_source_file"] = os.path.basename(f)
                        rows.append(r)
        return pd.DataFrame(rows)

    # ---- tables ------------------------------------------------------------
    def write(self, layer: str, name: str, df: pd.DataFrame) -> None:
        d = self.base / layer
        d.mkdir(parents=True, exist_ok=True)
        df.to_parquet(d / f"{name}.parquet", index=False)
        if layer == "gold":  # CSVs are the easiest thing to point Power BI at locally
            df.to_csv(d / f"{name}.csv", index=False)

    def read(self, layer: str, name: str) -> pd.DataFrame:
        return pd.read_parquet(self.base / layer / f"{name}.parquet")


class DatabricksStorage(LocalStorage):
    """Raw files sit in a UC Volume (FUSE-mounted, so pandas can read them).
    Tables are written as managed Delta tables so Power BI / SQL can query them."""

    def __init__(self, catalog: str, schema: str, volume: str = "raw"):
        from pyspark.sql import SparkSession  # only available on Databricks

        self.spark = SparkSession.builder.getOrCreate()
        self.catalog, self.schema = catalog, schema
        self.spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
        self.spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.{volume}")
        super().__init__(base=f"/Volumes/{catalog}/{schema}/{volume}")
        self.raw = self.base  # the volume root *is* the raw zone

    def write(self, layer: str, name: str, df: pd.DataFrame) -> None:
        table = f"{self.catalog}.{self.schema}.{layer}_{name}"
        out = df.copy()
        for c in out.columns:  # Spark wants plain Python strings/None in text columns
            if out[c].dtype == object or pd.api.types.is_string_dtype(out[c]):
                out[c] = out[c].astype(object).where(out[c].notna(), None).map(lambda v: v if v is None else str(v))
        sdf = self.spark.createDataFrame(out)
        (sdf.write.mode("overwrite").option("overwriteSchema", "true").format("delta").saveAsTable(table))

    def read(self, layer: str, name: str) -> pd.DataFrame:
        return self.spark.table(f"{self.catalog}.{self.schema}.{layer}_{name}").toPandas()


def get_storage(env: str = "auto", base: str = "data", catalog: str = "workspace",
                schema: str = "vendor_inventory"):
    if env == "databricks" or (env == "auto" and on_databricks()):
        return DatabricksStorage(catalog, schema)
    return LocalStorage(base)
