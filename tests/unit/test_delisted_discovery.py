"""Code-space sweep that reconstructs the delisted universe without a vendor list."""

from datetime import date

import polars as pl

from ashare_lake.config import Config
from ashare_lake.domain.symbols import ISSUED_CODE_BANDS, issued_code_space
from ashare_lake.steps.delisted import (
    catalog_path,
    discover_delisted,
    load_delisted_catalog,
    pending_codes,
)

# Codes verified against Sina during the source investigation.
_DELISTED = {
    "600001.SH": date(2009, 12, 15),
    "600002.SH": date(2006, 4, 6),
    "600005.SH": date(2017, 1, 23),
}
_NEVER_ISSUED = {"600013.SH", "600014.SH", "600024.SH"}


def _cfg(tmp_path, live=("600519.SH", "000001.SZ")):
    cfg = Config(data_root=tmp_path / "data", sources={"sina": True})
    part = cfg.curated_root / "instruments"
    part.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": list(live)}).write_parquet(part / "part-merged.parquet")
    return cfg


def _probe(symbol, client):
    if symbol in _DELISTED:
        return _DELISTED[symbol]
    return None


# --- code space -------------------------------------------------------------


def test_code_space_covers_every_band_without_duplicates():
    space = issued_code_space()
    expected = sum(last - first for _e, first, last in ISSUED_CODE_BANDS)

    assert len(space) == len(set(space)) == expected
    assert "600519.SH" in space and "000001.SZ" in space and "300750.SZ" in space
    # Zero-padded to six digits, else the symbol will not match instruments.
    assert "000001.SZ" in space and "1.SZ" not in space


def test_pending_excludes_symbols_that_are_listed_today(tmp_path):
    cfg = _cfg(tmp_path, live=("600001.SH", "600519.SH"))

    pending = pending_codes(cfg)

    assert "600001.SH" not in pending, "a live symbol is not a delisting candidate"
    assert "600002.SH" in pending


# --- sweep ------------------------------------------------------------------


def test_sweep_classifies_former_listings_and_never_issued(tmp_path):
    cfg = _cfg(tmp_path)

    result = discover_delisted(cfg, limit=40, probe=_probe)

    catalog = load_delisted_catalog(cfg)
    assert {"600001.SH", "600002.SH", "600005.SH"} <= set(catalog)
    assert catalog["600001.SH"] == date(2009, 12, 15)
    assert result.delisted == 3
    assert result.never_issued == result.probed - 3


def test_sweep_resumes_instead_of_reprobing(tmp_path):
    cfg = _cfg(tmp_path)
    first = discover_delisted(cfg, limit=40, probe=_probe)

    seen: list[str] = []

    def counting(symbol, client):
        seen.append(symbol)
        return _probe(symbol, client)

    discover_delisted(cfg, limit=40, probe=counting)

    assert first.probed == 40
    assert not set(seen) & set(load_delisted_catalog(cfg)), "already-classified codes reprobed"


def test_a_failing_probe_stays_pending_rather_than_being_filed(tmp_path):
    """Misfiling an outage as never-issued would shrink the universe permanently."""
    cfg = _cfg(tmp_path)
    calls = {"n": 0}

    def flaky(symbol, client):
        calls["n"] += 1
        if calls["n"] <= 5:
            raise ConnectionError("reset by peer")
        return _probe(symbol, client)

    result = discover_delisted(cfg, limit=20, probe=flaky)

    assert len(result.failed) == 5
    assert result.probed == 15
    still_pending = set(pending_codes(cfg))
    assert set(result.failed) <= still_pending


def test_catalog_survives_and_accumulates_across_sweeps(tmp_path):
    cfg = _cfg(tmp_path)
    discover_delisted(cfg, limit=5, probe=_probe)
    before = len(load_delisted_catalog(cfg)) + len(
        __import__("json").loads(catalog_path(cfg).read_text())["never_issued"]
    )

    discover_delisted(cfg, limit=5, probe=_probe)
    after = len(load_delisted_catalog(cfg)) + len(
        __import__("json").loads(catalog_path(cfg).read_text())["never_issued"]
    )

    assert before == 5
    assert after == 10


def test_sweep_reports_what_is_left(tmp_path):
    cfg = _cfg(tmp_path)

    result = discover_delisted(cfg, limit=10, probe=_probe)

    assert result.complete is False
    assert result.remaining == len(pending_codes(cfg))
    assert result.remaining > 0
