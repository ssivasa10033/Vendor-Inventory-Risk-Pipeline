"""vip-check-data: the gate between someone's own files and the pipeline."""

from vendor_pipeline.checkdata import check


def test_simulated_data_passes_the_contract(run_result):
    results = check(run_result["storage"].base)
    fails = [r for r in results if r["level"] == "FAIL"]
    assert not fails, fails
    # the cross-file consistency checks must have actually run
    assert any(r["source"] == "invoices ↔ catalog" for r in results)


def test_missing_and_malformed_sources_are_blocked(tmp_path):
    raw = tmp_path / "raw"
    (raw / "invoices").mkdir(parents=True)
    # wrong columns + unparseable date/money values
    (raw / "invoices" / "inv.csv").write_text(
        "invoice_number,date,sku,amount\nA1,13.05.2024,X9,ten dollars\n", encoding="utf-8"
    )
    results = check(tmp_path)
    by_source = {r["source"]: r for r in results}
    assert by_source["invoices"]["level"] == "FAIL"
    assert "missing columns" in by_source["invoices"]["message"]
    assert by_source["item_master.csv"]["level"] == "FAIL"  # absent source is blocking
    assert sum(r["level"] == "FAIL" for r in results) >= 9


def test_no_raw_folder_is_a_single_clear_failure(tmp_path):
    results = check(tmp_path / "nope")
    assert len(results) == 1 and results[0]["level"] == "FAIL" and "raw/" in results[0]["message"]
