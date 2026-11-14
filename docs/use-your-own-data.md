# Use the pipeline with your own data

The pipeline isn't tied to the simulator or to food service. Any business that **buys from vendors and
holds inventory** — a restaurant, a retail shop, a workshop, a clinic — produces the same six kinds of
records. Put yours in the layout below, and the same cleaning, risk scoring, and dashboard work unchanged.
Everything runs locally; your data never leaves your machine.

## The 60-second version

```bash
vip-refresh --base data                       # run the demo once: data/raw/ is now a template of every file
mkdir my-data && cp -r data/raw my-data/raw   # copy it, then replace the contents with your exports
vip-check-data --base my-data                 # validates structure, formats, and cross-file consistency
vip-run-pipeline --env local --base my-data   # bronze -> silver -> gold + quality gate
streamlit run dashboard/app.py                # sidebar -> "Use your own data" -> point it at my-data
```

Or do it entirely in the dashboard: sidebar → **Use your own data** → enter the folder → **Check my data**
→ fix anything ❌ → **Run pipeline & view my data**.

## The contract: ten sources under `raw/`

Four small reference tables (you maintain these — they're the pipeline's vocabulary) and six transaction
feeds (exported from whatever systems you have). Extra columns are fine everywhere; they're carried into
bronze and ignored. File names within a folder don't matter, only the folder and extension.

### Reference tables (one file each)

| File | Required columns | What it is |
|---|---|---|
| `reference/vendor_master.csv` | `vendor_id, vendor_name, payment_terms, primary_categories` | One row per supplier. `vendor_id` is yours to invent (`V01`…). |
| `reference/item_master.csv` | `item_id, item_name, category, base_uom, shelf_life_days, primary_vendor_id` | One row per thing you buy. `base_uom` is the single unit everything converts to (`kg`, `L`, `ea`). Use a large `shelf_life_days` (e.g. 365) for non-perishables. |
| `reference/vendor_catalog.csv` | `vendor_id, vendor_sku, vendor_description, item_id, invoice_uom, base_units_per_uom` | The Rosetta stone: maps each vendor's SKU and billing unit to your item and base unit. A case of 12×1L milk billed as `CS` → `base_units_per_uom = 12`. |
| `reference/item_aliases.csv` | `alias, item_id` | Every way an item's name appears on count sheets (`chkn brst` → `M01`). Add rows until the checker's coverage is ≥90%. |

### Transaction feeds (any number of files per folder)

| Folder | Required columns | Notes |
|---|---|---|
| `invoices/*.csv` | `invoice_number, invoice_date, po_ref, vendor_sku, description, qty, uom, unit_price, line_total, line_type, vendor_name` | One row per line item. `line_type`: `INV` for normal lines, `CR` for credits — if your system has no credits, set every row to `INV`. Messy is fine: `$1,234.50`, mixed date formats, inconsistent vendor spellings are exactly what silver cleans. |
| `purchase_orders/*.csv` | `po_number, vendor_id, order_date, expected_date, vendor_sku, qty_ordered, uom` | What you ordered and when it was promised. `expected_date` powers all on-time metrics. |
| `receiving/*.jsonl` | `po_number, vendor_sku, received_date, qty_received` | What actually arrived (one JSON object per line). This is the source of truth deliveries are judged against — short or late shipments show up by comparing it to the PO and invoice. |
| `inventory_counts/*.csv` | `count_date, item, qty, uom, counted_by` | Periodic hand counts. `item` is free text — the alias table maps it. Weekly counts give weekly usage; monthly counts give monthly. |
| `waste/*.csv` | `date, item, qty, uom, reason` | Spoilage/write-off log. Gaps are fine — unlogged waste is absorbed into derived usage as shrink. |
| `market/*.csv` | `month, item_id, benchmark_price` | Monthly benchmark per item (`month` as `YYYY-MM`). Optional column `source` labels where it came from. **No benchmark?** Simplest honest fallback: put your own average paid price per month here — the "premium vs market" reads 0% and every other metric still works. Canadian food businesses can run `vip-fetch-market` for real StatCan series. |

### Formats the cleaner accepts

* **Dates:** `2024-05-13`, `05/13/2024`, `13-May-2024`, `May 13 2024` (parsed explicitly — anything else
  is rejected rather than guessed, so `vip-check-data` tells you before the pipeline does). US-style
  month/day order is assumed for the slash format.
* **Money/quantities:** plain numbers or `"$1,234.50"`-style strings.
* **Units:** whatever appears in `uom` must be convertible via `vendor_catalog.base_units_per_uom`
  (plus built-in lb↔kg). Unconvertible rows are quarantined, not dropped.

## What `vip-check-data` verifies

1. **Structure** — every source present, required columns exist, files non-empty. ❌ blocks the run.
2. **Formats** — ≥95% of date values parse, ≥95% of money/qty values are numeric. ❌ blocks the run.
3. **Consistency** — invoice SKUs found in the catalog, count-sheet names covered by aliases, receipts
   matching POs (each ≥90%). ⚠️ warns: the pipeline will run, but sub-threshold rows land in quarantine.

## What's different with real data

* **`vip-validate` doesn't apply.** It scores the pipeline against the simulator's hidden answer key, which
  your data doesn't have. Your trust checks instead: the **quality gate** (runs automatically; fails the run
  if quarantine rates spike), the **quarantine tab** (read every `_reason` the first time — it's where source
  problems surface), and **reconciliation** (cleaned + quarantined spend should match your accounting total;
  that's the same arithmetic the money gate proves on simulated data).
* **Business calendar:** the demo excludes July–August from planning usage (its slow season) — the
  `~month.isin([7, 8])` filter in `gold.py` (search for "summer volumes"). Adjust or remove it to match
  your own seasonality.
* **Privacy:** nothing is uploaded anywhere. The dashboard, pipeline, and checker are all local. Don't
  commit your `my-data/` folder to a public repo (`.gitignore` already excludes `data/`; add your folder too).
