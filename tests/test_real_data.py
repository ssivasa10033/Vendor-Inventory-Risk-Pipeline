"""The StatCan download can't run in every sandbox, so test the parser and the mapping against a
small file in the exact full-table CSV format StatCan publishes."""

import io
import zipfile

from vendor_pipeline import real_data, simulate
from vendor_pipeline.config import ITEM_BY_ID

HEADER = "REF_DATE,GEO,DGUID,{dim},UOM,UOM_ID,SCALAR_FACTOR,SCALAR_ID,VECTOR,COORDINATE,VALUE,STATUS,SYMBOL,TERMINATED,DECIMALS"


def _csv(dim, rows):
    lines = [HEADER.format(dim=dim)]
    for month, geo, product, value in rows:
        lines.append(f'{month},{geo},2016A000235,"{product}",Dollars,81,units,0,v1,1.1,{value},,,,2')
    return "\n".join(lines)


def _zip(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("18100245.csv", "﻿" + text)
        z.writestr("18100245_MetaData.csv", "meta")
    return buf


MONTHS = ["2022-12", "2023-01", "2023-02", "2024-01", "2025-06"]
AVG = _csv("Products", [
    *[(m, "Ontario", "Chicken breasts, per kilogram", v) for m, v in zip(MONTHS, [15.0, 16.0, 16.4, 17.6, 18.4])],
    *[(m, "Canada", "Chicken breasts, per kilogram", 99) for m in MONTHS],           # wrong geo: ignored
    *[(m, "Ontario", "Frozen chicken breasts, per kilogram", 30) for m in MONTHS],  # excluded
    *[(m, "Ontario", "Bananas, per kilogram", v) for m, v in zip(MONTHS, [1.6, 1.7, 1.7, 1.75, 1.8])],
    *[(m, "Ontario", "Plant-based milk, 1.89 litres", 5) for m in MONTHS],
])
CPI = _csv("Products and product groups", [
    *[(m, "Ontario", "Food purchased from stores", v) for m, v in zip(MONTHS, [180, 182, 183, 190, 196])],
    *[(m, "Ontario", "All-items", v) for m, v in zip(MONTHS, [155, 156, 157, 161, 165])],
])


def test_reads_zip_and_filters_geo():
    df = real_data.read_statcan_csv(_zip(AVG), "Ontario")
    assert set(df["product"]) >= {"Chicken breasts, per kilogram", "Bananas, per kilogram"}
    assert (df["value"] != 99).all()


def test_index_anchored_at_jan_2023_with_fallbacks():
    avg = real_data.read_statcan_csv(io.StringIO(AVG), "Ontario")
    cpi = real_data.read_statcan_csv(io.StringIO(CPI), "Ontario")
    idx = real_data.build_index(avg, cpi, "Ontario")
    m01 = idx[idx["item_id"] == "M01"].set_index("month")
    assert m01.loc["2023-01", "index_value"] == 1.0
    assert abs(m01.loc["2025-06", "index_value"] - 18.4 / 16.0) < 1e-4
    assert "Frozen" not in m01["source"].iloc[0]
    assert "CPI" in idx.loc[idx["item_id"] == "S01", "source"].iloc[0]   # spices -> CPI food
    assert "All-items" in idx.loc[idx["item_id"] == "K01", "source"].iloc[0]  # packaging -> CPI all-items
    assert "2022-12" not in set(idx["month"])


def test_simulator_uses_real_index(tmp_path):
    avg = real_data.read_statcan_csv(io.StringIO(AVG), "Ontario")
    idx = real_data.build_index(avg, None, "Ontario")
    from datetime import date
    sim = simulate.simulate(date(2023, 1, 2), date(2023, 3, 31), seed=1, real_index=idx)
    mk = sim["market"].set_index(["item_id", "month"])
    assert abs(mk.loc[("M01", "2023-02"), "benchmark_price"] - ITEM_BY_ID["M01"].base_price * 16.4 / 16.0) < 1e-3
    assert mk.loc[("M01", "2023-03"), "source"].endswith("(carried forward)")
    assert mk.loc[("G01", "2023-02"), "source"] == "simulated"
