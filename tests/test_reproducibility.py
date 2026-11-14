"""Same seed + same code = same answers, and extending the date range never rewrites history."""

import filecmp
from datetime import date

from vendor_pipeline import pipeline, simulate
from vendor_pipeline.storage import LocalStorage
from vendor_pipeline.validate import GOLD_TABLES, fingerprint


def _run(base, end):
    st = LocalStorage(base)
    simulate.generate(st.raw, date(2024, 1, 1), end, seed=11)
    return st


def test_history_is_stable_when_end_date_moves(tmp_path):
    a = _run(tmp_path / "a", date(2024, 9, 30))
    b = _run(tmp_path / "b", date(2024, 12, 31))
    for folder, prefix in [("invoices", "invoices"), ("purchase_orders", "po"), ("inventory_counts", "counts")]:
        for month in ["2024_01", "2024_05", "2024_08"]:
            ext = "csv"
            fa, fb = a.raw / folder / f"{prefix}_{month}.{ext}", b.raw / folder / f"{prefix}_{month}.{ext}"
            assert filecmp.cmp(fa, fb, shallow=False), f"{fa.name} changed when the end date moved"


def test_same_seed_same_gold_fingerprint(tmp_path):
    fps = []
    for run in ("r1", "r2"):
        st = _run(tmp_path / run, date(2025, 2, 28))
        g = pipeline.run(st)["gold"]
        fps.append(fingerprint({t: g[t] for t in GOLD_TABLES}))
    assert fps[0] == fps[1]
