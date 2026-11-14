# Setup guide

Six sections: IDE → local dashboard → GitHub repo → accuracy proof → reproducibility → live use and real data.

---

## 1. Set it up in your IDE (VS Code)

**Prereqs:** Python 3.10–3.12, Git, VS Code. On Windows, use PowerShell or Git Bash.

```bash
# from the unzipped folder
python -m venv .venv
# macOS/Linux:   source .venv/bin/activate
# Windows:       .venv\Scripts\Activate.ps1
pip install -e ".[dev,dashboard]" -c requirements-lock.txt
code .
```

When VS Code opens:

1. **Install the recommended extensions** from the popup (Python, Ruff, Databricks, YAML, GitHub Actions). The list is in `.vscode/extensions.json`.
2. **Pick the interpreter:** `Ctrl/Cmd+Shift+P` → *Python: Select Interpreter* → `.venv`. On Windows, change `python.defaultInterpreterPath` in `.vscode/settings.json` to `.venv\\Scripts\\python.exe`.
3. **Run things without typing commands:** `Terminal → Run Task…`

   | Task | What it does |
   |---|---|
   | **Full refresh** (`Ctrl/Cmd+Shift+B`) | land simulated data → pipeline → validation |
   | Full refresh - LIVE | simulates through yesterday, with real StatCan prices if reachable |
   | Dashboard | opens the Streamlit dashboard on http://localhost:8501 |
   | Tests | the 23 pytest tests (also visible in the Testing sidebar) |
   | Fetch real StatCan prices | downloads and maps the real market data |

4. **Debug:** the Run & Debug panel has *Debug: run pipeline*. Put a breakpoint in `silver.py` and inspect the DataFrames as each cleaning rule runs. This is useful footage for the video.

**Zero-install option:** push to GitHub, then *Code → Codespaces → Create*. The `.devcontainer` installs everything, runs the pipeline, and forwards the dashboard port.

---

## 2. See a working dashboard before Power BI

```bash
vip-refresh --base data          # once, ~15s
streamlit run dashboard/app.py   # opens http://localhost:8501
```

There are five tabs:

* **Overview:** KPI tiles, monthly spend, and "what to do this week"
* **Vendors:** risk scores with the top driver, and trends for on-time, OTIF and fill rate
* **Prices vs market:** what was paid vs the benchmark, with its source shown
* **Inventory:** reorder points, plus usage rebuilt from counts
* **Data quality & accuracy:** what was quarantined, the quality gate, and the validation scores

It reads the same `data/gold/*.parquet` tables Power BI will use. For Episode 2, Power BI either imports `data/gold/*.csv` or connects to the Databricks SQL warehouse. The dashboard's layout is a ready-made spec to rebuild in Power BI.

**Share it publicly (free):** push the repo, go to share.streamlit.io → *New app* → pick `dashboard/app.py`. Under *Advanced settings → Secrets / env*, set `VIP_LIVE = "1"`. Live mode simulates through yesterday with real StatCan prices, so the link always shows current data.

---

## 3. Create the GitHub repo

With the GitHub CLI (`gh auth login` first):

```bash
git init -b main
git add .
git commit -m "Vendor & inventory risk pipeline: simulator, medallion pipeline, validation, dashboard, CI/CD"
gh repo create vendor-inventory-pipeline --public --source=. --push \
  --description "Messy vendor/inventory data -> risk metrics. Pandas, Databricks, GitHub Actions, Streamlit."
```

Without the CLI: create an empty repo on github.com (no README), then
`git remote add origin https://github.com/ssivasa10033/Vendor-Inventory-Risk-Pipeline.git && git push -u origin main`.

Then, in the repo settings:

* **Secrets** (*Settings → Secrets and variables → Actions*): `DATABRICKS_HOST`, `DATABRICKS_TOKEN`. Until you add them, the deploy job skips itself.
  `gh secret set DATABRICKS_HOST` / `gh secret set DATABRICKS_TOKEN` works too.
* **Branch protection** (*Settings → Branches → main*): require the `test`, `reproducibility` and `lint` checks. This proves on camera that nothing merges without passing.
* **About section:** add topics `data-engineering`, `databricks`, `pandas`, `github-actions`, `streamlit`. Put the Streamlit link in the website field.
* Add a CI badge to the README:
  `![CI](https://github.com/ssivasa10033/Vendor-Inventory-Risk-Pipeline/actions/workflows/ci-cd.yml/badge.svg)`

---

## 4. How you know it's accurate, and how to prove it

The simulator's advantage: **the true answers are known.** Real usage, true waste, every injected error and every overbilled line are written to `raw/_truth/`. The pipeline never reads that folder. `vip-validate` then scores the pipeline against it (report: `docs/validation_report.md`):

| What's checked | How | Current result |
|---|---|---|
| Cleaning catches the injected errors | precision/recall per error type (duplicates, decimal typos, negative qty, missing prices, blank counts, unknown names) | 100% / 100% on every type |
| Money reconciles | cleaned spend + quarantined spend vs true invoice total | 0.0002% unexplained |
| Usage reconstruction is right | weekly derived usage vs true stock drawdown | 5.4% WMAPE, r = 0.996 |
| Vendor metrics measure what's real | measured on-time and in-full rates vs the generator's parameters, as a z-test | all \|z\| < 3 (within sampling error) |
| Billing exceptions are real | flagged vs truly overbilled lines | 100% precision, 100% recall |
| Stockout signal works | emergency buys vs true stockout days (rank correlation) | ρ = 0.84 |

**What it proves, and what it doesn't.** It proves the *code* is correct: given data with these problems, it gets the right answers. It doesn't prove the risk-score *weights* are optimal. Those are judgment calls, which is why they're fixed, written down, and shown per component. Say this on camera; it's the kind of distinction interviewers like to hear.

How to show it in the video:

* Run `vip-validate` and show the green report.
* **Break something on purpose.** Lower the fuzzy-match cutoff in `silver.py` from 0.6 to 0.3, or delete the duplicate check. Rerun and show the gate fail. Then show CI turn red on a PR.
* Run it over different seeds (`vip-refresh --seed 1`, `--seed 2`…). It passes on all of them, so the result isn't cherry-picked.

---

## 5. Is it reproducible in other environments?

Yes, and CI proves it on every push:

* **Pinned versions** in `requirements-lock.txt`, and a fixed random seed.
* **The CI matrix** runs tests, the pipeline and validation on **Ubuntu, Windows and macOS × Python 3.10 and 3.12**.
* **A fingerprint** (SHA-256 of every gold table in canonical form) is computed in each environment. The `reproducibility` job fails unless all six match, and it writes a table of them to the run summary.
  The current local fingerprint is `9f0e8c244f097eef`; the CI run summary shows all six environments matching it on every push.
* **Stable history:** extending the end date never changes earlier files (each month has its own random stream). A test enforces this, which is what makes the daily live mode trustworthy.
* **Same code, three runtimes:** your laptop, GitHub Actions, and Databricks serverless (only `storage.py` differs).
* **Devcontainer** for an identical environment anywhere.

---

## 6. Real-time use cases and real data

**What's real:** market prices. `real_data.py` pulls two Statistics Canada open-data tables:

* **18-10-0245-01**, monthly average retail prices (Ontario): chicken breast, ground beef, bananas, milk, eggs…
* **18-10-0004-01**, CPI: the fallback for items without an average-price series (spices → "Food purchased from stores", packaging → "All-items")

Retail shelf prices aren't what a caterer pays a distributor, so the pipeline uses the real **movement**, not the level: benchmark = the Jan-2023 wholesale price × (StatCan price this month ÷ StatCan price Jan 2023). Simulated vendor prices then follow real-world swings, and every benchmark row records its exact StatCan series (visible on the dashboard). Run `vip-fetch-market` to see the mapping.

**What stays simulated, and why:** no public dataset has a real restaurant's invoices, deliveries and hand counts together. That data is private everywhere, including the original job. Simulating it isn't a shortcut; it's the only way to *measure* accuracy (section 4). Say both halves on camera.

**Real-time use cases built in:**

| Use case | Where |
|---|---|
| Every morning: land yesterday's deliveries, rebuild all tables, re-score accuracy | `.github/workflows/live.yml` (6:17am Toronto) and the Databricks job (6:00am, prod target runs `--end today`) |
| Morning brief: KPIs and the top actions posted to the run summary | the live workflow's *Publish headline numbers* step |
| "Reorder now: ~19.9 kg to reach target" | `gold_inventory_health.recommended_action`, recalculated daily from inventory position |
| Catch a supplier getting worse before it hurts | `on_time_trend_6m` and the vendor risk score (Spice Route drifts down every year) |
| Catch a vendor billing for shorted product | `gold_billing_exceptions`, the moment the invoice lands |
| Know when you're overpaying vs the market | `item_price_monthly.premium_vs_market_pct` against real StatCan movement |
| Always-current public demo | the Streamlit app with `VIP_LIVE=1` |

**Caveats:**

* StatCan publishes monthly with a lag of about 1–2 months. The latest real month is carried forward and labeled "(carried forward)".
* Databricks Free Edition limits outbound internet to trusted domains. If StatCan is blocked there, `--market auto` falls back to simulated prices and logs it. GitHub Actions and your laptop can reach StatCan.
* This sandbox couldn't reach StatCan, so the fetcher was tested against files in StatCan's exact published CSV format (`tests/test_real_data.py`). Run `vip-fetch-market` once on your machine and check the mapping it prints. If StatCan has renamed a product, adjust the regex in `ITEM_MATCH`.
