import pandas as pd
import pytest

from vendor_pipeline.silver import build_vendor_matcher, normalize_uom, parse_dates, parse_money

VENDORS = pd.DataFrame({
    "vendor_id": ["V01", "V03", "V06", "V08"],
    "vendor_name": ["Northfield Foodservice", "Lakeshore Meats", "GTA Packaging Supply", "Bargain Cash & Carry"],
})


@pytest.mark.parametrize("raw,expected", [
    ("NORTHFIELD FOODSERVICE INC", "V01"),
    ("northfield", "V01"),
    ("LAKESHORE MEATS & PROVISIONS", "V03"),
    ("G.T.A. PACKAGING", "V06"),
    ("bargain cash&carry", "V08"),
    ("Totally Unknown Supplier", None),
])
def test_vendor_matching(raw, expected):
    assert build_vendor_matcher(VENDORS)(raw) == expected


def test_parse_dates_handles_every_export_format():
    s = pd.Series(["2024-03-05", "03/05/2024", "05-Mar-2024", "Mar 05 2024", "garbage"])
    out = parse_dates(s)
    assert (out.iloc[:4] == pd.Timestamp("2024-03-05")).all()
    assert pd.isna(out.iloc[4])


def test_parse_money():
    out = parse_money(pd.Series(["$1,234.50", "12.4", None, " $7 "]))
    assert out.iloc[0] == 1234.50 and out.iloc[1] == 12.4 and pd.isna(out.iloc[2]) and out.iloc[3] == 7


def test_normalize_uom():
    assert list(normalize_uom(pd.Series(["lbs", "Case", "each", "KG", "dozen"]))) == ["LB", "CS", "EA", "KG", "DZ"]
