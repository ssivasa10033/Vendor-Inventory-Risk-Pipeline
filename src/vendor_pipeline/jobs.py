"""Entry points. Same commands locally, in GitHub Actions, and as Databricks job tasks.

    vip-land-raw      --env local|databricks   # generate simulated vendor exports into the raw zone
    vip-run-pipeline  --env local|databricks   # raw -> bronze -> silver -> gold + quality gate
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import pipeline, real_data, simulate
from .config import DEFAULT_SEED, SIM_END, SIM_START
from .storage import get_storage


def _args(argv, extra=None):
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="auto", choices=["auto", "local", "databricks"])
    p.add_argument("--base", default="data", help="local data folder")
    p.add_argument("--catalog", default="workspace")
    p.add_argument("--schema", default="vendor_inventory")
    if extra:
        extra(p)
    return p.parse_args(argv)


def land_raw_main(argv=None):
    def extra(p):
        p.add_argument("--start", default=SIM_START.isoformat())
        p.add_argument("--end", default=SIM_END.isoformat(), help="YYYY-MM-DD, or 'today' for live mode")
        p.add_argument("--seed", type=int, default=DEFAULT_SEED)
        p.add_argument("--market", default="auto", choices=["auto", "real", "simulated"],
                       help="market benchmark: real StatCan prices, simulated, or auto (real if reachable)")
        p.add_argument("--scale", type=float, default=1.0,
                       help="business size: demand multiplier (1.0 = ~$335k/yr kitchen, 0.25 = sub-$100k/yr)")

    a = _args(argv if argv is not None else sys.argv[1:], extra)
    st = get_storage(a.env, a.base, a.catalog, a.schema)
    real_index = real_data.load_or_fetch(Path(st.base) / "external", a.market)
    stats = simulate.generate(st.raw, date.fromisoformat(a.start), simulate.parse_end(a.end), a.seed, real_index,
                              scale=a.scale)
    print(f"landed raw exports in {st.raw}: {stats}")


def run_pipeline_main(argv=None):
    a = _args(argv if argv is not None else sys.argv[1:])
    st = get_storage(a.env, a.base, a.catalog, a.schema)
    pipeline.run(st)


def refresh_main(argv=None):
    """land + run + validate in one go (local only). Extra args go to the land step, e.g. --end today."""
    argv = list(argv if argv is not None else sys.argv[1:])
    base = argv[argv.index("--base") + 1] if "--base" in argv else "data"
    land_raw_main(["--env", "local", *argv])
    run_pipeline_main(["--env", "local", "--base", base])
    from . import validate
    validate.main(["--base", base])


if __name__ == "__main__":
    {"land": land_raw_main, "run": run_pipeline_main, "refresh": refresh_main}[sys.argv.pop(1)]()
