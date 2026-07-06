#!/usr/bin/env python3
"""Full init from 2016-01-01: L0/L1/L2 + adj_factors (see job.init.phases)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import stock_data_engine.steps  # noqa: F401 — register steps
from stock_data_engine.config import load_config
from stock_data_engine.orchestrator.engine import JobEngine

CONFIG = ROOT / "configs" / "stockdata.toml"
# As-of last trading day in seed calendar (2026-07-06 = Monday, is_trading)
TRADE_DATE = date(2026, 7, 6)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = load_config(CONFIG)
    logging.info("data_root=%s trade_date=%s workers=%s", cfg.data_root, TRADE_DATE, cfg.workers)
    engine = JobEngine(cfg)
    result = engine.run_init_phases(trade_date=TRADE_DATE)
    print(json.dumps(result, indent=2, default=str))
    failed = [p for p in result.get("phases", []) if p.get("status") != "success"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
