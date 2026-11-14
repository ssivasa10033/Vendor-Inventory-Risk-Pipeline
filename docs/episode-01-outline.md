# Episode 1: Turning messy vendor data into a supplier risk pipeline (Python + Databricks + CI/CD)

**Target length:** 17–19 min · **Audience:** students and junior devs who know some Python but have never built a "real" data pipeline
**Promise to the viewer:** "By the end you'll know how to take ugly exports from a real small business and turn them into tables a manager can make decisions from, running automatically every morning."

---

## 0:00 – Cold open (30s)
**Screen:** split view. Left: a raw invoice CSV with `NORTHFIELD FOODSERVICE INC`, `$1,234.50`, `02/07/2025`. Right: the final vendor risk table sorted by score.
**Say:** "Left is what a small catering business actually has. Right is what the new owners used to decide who to buy from. Here's how to get from one to the other."

## 0:30 – The real story and the honesty disclaimer (1 min)
- Fall 2025: a family catering kitchen + café changed owners. The previous owner's vendor and inventory history was a pile of exports.
- My job: make it usable. Which vendors are risky, what to reorder, where money leaks.
- **Say clearly:** "That data belongs to the business, so for this video I built a simulator that recreates the same problems with fake data. Same mess, same techniques."

## 1:30 – Meet the mess (2 min) ← *the hook; spend time here*
Open `data/raw/` in VS Code and show, in order:
1. **One vendor, four names:** `Northfield Foodservice` / `NORTHFIELD FOODSERVICE INC` / `northfield`. A naive `groupby` would show four suppliers.
2. **Dates:** `2025-04-19`, `02/07/2025`, `01-Apr-2025` in the same column. Ask the viewer: "Is 02/07 February or July?" Then: "That's why we never let pandas guess."
3. **Units:** meat billed per lb, counted in kg or lb, produce in cases. "You can't compare anything until everything is in one unit."
4. **Count sheets:** `chkn brst`, `  AP FLOUR `, `cukes`.
5. **The big one:** "There is no usage data. The kitchen cooks from memory. We have to *reconstruct* how much they used."

## 3:30 – Architecture in one slide (1 min)
Draw raw → bronze → silver → gold → quality gate (README diagram).
- **Bronze** = exact copy plus where/when it came from ("so you can always replay")
- **Silver** = cleaned, one ID per vendor and item, one unit, bad rows quarantined
- **Gold** = one table per business question
- "This is the medallion architecture. Databricks didn't invent the idea, but they named it."

## 4:30 – Simulator, quickly (1.5 min)
`simulate.py`. Don't read the code, just explain the model:
- Demand is weekday-heavy, drops in July/August (schools out), and spikes in December.
- Each vendor has order days, a lead time, and a reliability that can **drift**. Spice Route slides from about 64% on time in 2023 to about 54% after that.
- Real-life rule: the *truth* (actual usage) is hidden. Only the messy exports come out.
- "If you want realistic practice data, simulate the process, not the spreadsheet."

## 6:00 – Silver: cleaning live (4 min) ← *core teaching segment*
Walk through `silver.py`, one technique at a time, showing a before/after `df.head()` for each:
1. `normalize_vendor_name` + `difflib.get_close_matches`, then **cross-check against the SKU's vendor**. "Two sources agreeing is how you trust a fuzzy match."
2. `parse_dates`: an explicit format list.
3. `parse_money`: strip `$` and `,`.
4. Duplicate detection (re-sent invoices) and price imputation from line totals, **flagged** with `price_imputed`.
5. Unit conversion through the vendor catalog → `qty_base`, `unit_cost_base`.
6. Rolling-median outlier check catches decimal-shift typos ($4.20 → $42.00).
7. **Quarantine, not delete.** Show `silver_quarantine` grouped by `_reason`.
   Line: "If you delete bad rows, nobody ever fixes the source."

## 10:00 – Gold: metrics that mean something (3 min)
- **Derived usage:** `opening + received − waste − closing`. Show the test that compares it to the hidden truth (corr > 0.9). "This is how you prove a metric you can't directly measure."
- **Vendor risk score:** six signals with fixed thresholds. Show Lakeshore Meats as High because ~43% of all spend goes to one supplier (dependency), plus overbilling. Spice Route is High on delivery.
- **Billing exceptions:** "Lakeshore billed for product it shorted 25 times."
- **Inventory health:** safety stock formula on screen, reorder point vs **inventory position**, ABC class.
  Explain why July/August are excluded from planning usage: "The handover was in August. Planning off summer numbers would starve September."
- Read out 3–4 `recommended_action` rows. "This is the part the owner actually reads."

## 12:30 – "How do I know it's right?" (1.5 min) ← *credibility segment*
- Run `vip-validate`. The simulator hid the true answers; the pipeline never saw them.
- Show the report: 100% of injected errors caught, spend reconciles to 0.0002%, usage within ~5% WMAPE, vendor rates within sampling error.
- Break it on purpose (lower the fuzzy-match cutoff), rerun, and show the gate go red.
- One line: "Simulated data isn't a weakness here. It's the only way to *measure* accuracy, because you know the answer key."

## 14:00 – Databricks (2 min)
- Free Edition, serverless, nothing to configure.
- `databricks bundle deploy -t dev`, then show the job in the Jobs UI with its two tasks and the 6am schedule.
- Open `notebooks/01_explore_gold.py`, run the quarantine and vendor risk cells.
- "Same code runs on my laptop and on Databricks. Only the storage class changes." Show `storage.py`.

## 16:00 – CI/CD with GitHub Actions (2 min)
- Open `.github/workflows/ci-cd.yml`: lint → tests → full pipeline run → deploy on `main`.
- **Demo the gate:** push a branch that breaks something (e.g., delete an alias line, or set the quarantine threshold to 0.1%) and show the red X. Revert and show it go green.
- "The pipeline can't ship bad data, because CI runs the quality gate on every push."
- Show the reproducibility job's summary: six environments (3 OSes × 2 Pythons), one fingerprint.

## 18:00 – Wrap and tease Episode 2 (30s)
- Flash the Streamlit dashboard (live link in the description).
- Recap: messy → trusted → metrics → automated.
- "Next episode we plug these gold tables into Power BI and build the dashboard the owners actually used: vendor risk, inventory health, and price vs market."

---

## Recording checklist
- [ ] Fresh run: `rm -rf data && vip-land-raw --env local && vip-run-pipeline --env local`
- [ ] VS Code font size ≥ 18, hide the sidebar for code segments
- [ ] Databricks job already deployed and run once (cold serverless start takes a minute; cut the wait)
- [ ] A broken-branch commit prepared for the CI demo
- [ ] Repo link and timestamps in the description

## B-roll / overlays
- Overlay the safety stock formula when it's explained
- Before/after table pairs for each cleaning step
- Counter graphic: "29 ways to spell 8 vendors"
