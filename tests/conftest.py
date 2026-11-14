from datetime import date

import pytest

from vendor_pipeline import pipeline, simulate
from vendor_pipeline.storage import LocalStorage


@pytest.fixture(scope="session")
def run_result(tmp_path_factory):
    """Simulate ~14 months (enough for 12-month windows), run the full pipeline once."""
    base = tmp_path_factory.mktemp("data")
    st = LocalStorage(base)
    simulate.generate(st.raw, date(2024, 6, 1), date(2025, 8, 31), seed=7)
    result = pipeline.run(st)
    result["storage"] = st
    return result
