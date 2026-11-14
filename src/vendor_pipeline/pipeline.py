"""Run raw -> bronze -> silver -> gold, then gate on data quality."""

from __future__ import annotations

import time

import pandas as pd

from . import bronze, gold, quality, silver


class DataQualityError(RuntimeError):
    pass


def run(storage, fail_on_error: bool = True) -> dict:
    t0 = time.time()
    b = bronze.ingest(storage)
    print(f"[bronze] {sum(len(v) for v in b.values()):,} rows from {len(b)} sources")
    s = silver.build(b, storage)
    print(f"[silver] {len(s['fact_invoice_line']):,} invoice lines, {len(s['fact_delivery']):,} delivery lines, "
          f"{len(s['quarantine']):,} quarantined")
    g = gold.build(s, storage)
    print(f"[gold]   {len(g)} tables, as of {g['kpi_summary']['as_of'].iloc[0]:%Y-%m-%d}")

    checks = quality.run_checks(b, s, g)
    checks["run_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    storage.write("gold", "dq_results", checks)
    for r in checks.itertuples():
        flag = "PASS" if r.passed else ("FAIL" if r.level == "error" else "WARN")
        print(f"  [{flag}] {r.check} {r.detail}")
    failed = checks[(~checks["passed"]) & (checks["level"] == "error")]
    print(f"done in {time.time() - t0:.1f}s")
    if fail_on_error and len(failed):
        raise DataQualityError(f"{len(failed)} data quality check(s) failed: {list(failed['check'])}")
    return dict(bronze=b, silver=s, gold=g, checks=checks)
