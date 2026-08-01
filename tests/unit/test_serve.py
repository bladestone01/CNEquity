"""The read-only dashboard API."""

from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl
import pytest
from fastapi.testclient import TestClient

from ashare_lake.serve.app import create_app
from ashare_lake.storage.stats import rebuild_stats

FETCHED = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def _write(root, partition: str | None, rows: list[dict]) -> None:
    target = root if partition is None else root / partition
    target.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(target / "part-0.parquet")


def _meta(source: str = "exchange_calendar") -> dict:
    return {"source": source, "data_version": "v1", "fetched_at": FETCHED}


def _row(symbol: str, day: date, source: str = "tdx_protocol") -> dict:
    # trade_date is not decoration: with no curated trading_calendar,
    # is_trading_day derives the sessions from daily_bars itself.
    return {
        "symbol": symbol,
        "trade_date": day,
        "source": source,
        "data_version": "v2",
        "fetched_at": FETCHED,
    }


@pytest.fixture
def lake(config):
    """A tiny lake with two datasets in different tiers, measured."""
    last, prev = date(2026, 7, 31), date(2026, 7, 30)
    _write(config.curated_root / "daily_bars", f"trade_date={last}", [_row("600519.SH", last)])
    _write(
        config.curated_root / "daily_bars",
        f"trade_date={prev}",
        [_row("600519.SH", prev), _row("000001.SZ", prev, source="ths")],
    )
    _write(config.curated_root / "instruments", None, [_row("600519.SH", last)])
    # The heatmap's x-axis is the trading calendar; without one it has no days
    # to draw and every assertion about cells would pass vacuously.
    _write(
        config.curated_root / "trading_calendar",
        "trade_date=2026",
        [
            {"trade_date": prev, "is_trading": True, **_meta()},
            {"trade_date": last, "is_trading": True, **_meta()},
        ],
    )
    rebuild_stats(config)
    return config


@pytest.fixture
def client(lake):
    return TestClient(create_app(lake))


def test_health_counts_every_registered_dataset(client):
    body = client.get("/api/health").json()
    from ashare_lake.domain.datasets import DATASETS

    assert body["datasets"] == len(DATASETS)
    assert body["fresh"] + body["stale"] + body["empty"] + body["not_applicable"] == len(DATASETS)
    # 3 daily_bars + 1 instruments + 2 trading_calendar
    assert body["rows"] == 6


def test_health_separates_optional_and_required_empties(client):
    """An opt-in dataset nobody enabled looks identical on disk to a failure."""
    body = client.get("/api/health").json()
    assert "minute_bars" in body["empty_optional"]
    assert "minute_bars" not in body["empty_required"]
    assert not set(body["empty_optional"]) & set(body["empty_required"])


def test_tiers_partition_the_datasets(client):
    tiers = client.get("/api/tiers").json()
    datasets = client.get("/api/datasets").json()
    members = [name for tier in tiers for name in tier["members"]]
    assert sorted(members) == sorted(d["dataset"] for d in datasets)
    assert len(members) == len(set(members))
    for tier in tiers:
        assert tier["datasets"] == len(tier["members"])


def test_tier_rows_sum_to_the_lake_total(client):
    tiers = client.get("/api/tiers").json()
    health = client.get("/api/health").json()
    assert sum(t["rows"] for t in tiers) == health["rows"]
    assert sum(t["bytes"] for t in tiers) == health["bytes"]


def test_datasets_can_be_filtered_to_one_tier(client):
    rows = client.get("/api/datasets", params={"tier": "L1"}).json()
    assert rows
    assert {r["tier"] for r in rows} == {"L1"}
    assert "daily_bars" in {r["dataset"] for r in rows}


def test_dataset_rows_carry_registry_and_measurement(client):
    rows = {r["dataset"]: r for r in client.get("/api/datasets").json()}
    bars = rows["daily_bars"]
    assert (bars["tier"], bars["granularity"], bars["freshness"]) == ("L1", "day", "fresh")
    assert bars["row_count"] == 3
    assert rows["instruments"]["granularity"] is None  # merge-style


def test_provenance_splits_one_dataset_by_source(client):
    rows = client.get("/api/datasets/daily_bars/provenance").json()
    assert {r["source"]: r["row_count"] for r in rows} == {"tdx_protocol": 2, "ths": 1}
    assert all(r["data_version"] == "v2" for r in rows)


def test_provenance_rejects_an_unregistered_dataset(client):
    assert client.get("/api/datasets/nope/provenance").status_code == 404


def test_heatmap_cells_are_one_char_per_day(client):
    body = client.get("/api/heatmap", params={"days": 5}).json()
    width = len(body["days"])
    assert width <= 5
    for row in body["rows"]:
        assert len(row["cells"]) == width
        assert set(row["cells"]) <= set(body["legend"])


def test_heatmap_marks_unpartitioned_datasets_apart_from_gaps(client):
    """instruments has no per-day notion; that is not the same as missing."""
    rows = {r["dataset"]: r for r in client.get("/api/heatmap").json()["rows"]}
    assert set(rows["instruments"]["cells"]) == {"-"}
    assert rows["instruments"]["granularity"] is None


def test_heatmap_carries_cadence_so_gaps_can_be_read_honestly(client):
    """A quarterly source's holes are its schedule, not a fault to colour red."""
    rows = {r["dataset"]: r for r in client.get("/api/heatmap").json()["rows"]}
    assert rows["daily_bars"]["cadence_days"] == 1
    assert rows["northbound_holdings"]["cadence_days"] == 100


def test_heatmap_rejects_an_absurd_window(client):
    assert client.get("/api/heatmap", params={"days": 0}).status_code == 422
    assert client.get("/api/heatmap", params={"days": 10_000}).status_code == 422


# --- the read-only and auth contracts ----------------------------------------


def test_no_route_can_mutate_the_lake(client):
    """The dashboard shows; the CLI acts. Guard the boundary, not the intent."""
    methods = set()
    for route in client.app.routes:
        methods |= set(getattr(route, "methods", set()))
    assert methods <= {"GET", "HEAD"}, f"a mutating method is routed: {methods}"


def test_a_token_is_required_when_one_is_configured(lake):
    client = TestClient(create_app(lake, token="s3cret"))
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    # The page fetches with a query token; a browser cannot set a header.
    assert client.get("/api/health", params={"token": "s3cret"}).status_code == 200
    assert client.get("/api/health", params={"token": "wrong"}).status_code == 401


def test_the_page_is_served_and_self_contained(client):
    body = client.get("/").text
    assert "<title>ashare-lake</title>" in body
    for external in ("http://", "https://", "//cdn", "<script src"):
        assert external not in body, f"page reaches outside for {external!r}"


def test_a_non_loopback_bind_demands_a_token():
    from click.testing import CliRunner

    from ashare_lake.cli.main import cli

    result = CliRunner().invoke(cli, ["serve", "--host", "0.0.0.0", "--config", "nope.toml"])
    assert result.exit_code != 0
    # Fails on the bind guard, not later on the missing config.
    assert "--token" in result.output


def test_stats_are_refreshed_in_the_background_when_the_lake_moves(lake):
    from ashare_lake.orchestrator.manifest import Manifest
    from ashare_lake.serve.lake import LakeView

    view = LakeView(lake)
    assert view.refresh_stats_in_background() is False  # already current

    Manifest(lake.manifest_path).start_run("daily")
    assert view.refresh_stats_in_background() is True


def test_an_unmeasured_lake_still_answers(config):
    """No meta/stats yet: rows are unknown, but nothing errors.

    Driven through LakeView rather than the endpoint because /api/health kicks
    off the background rebuild, which would race this to the assertion.
    """
    from ashare_lake.serve.lake import LakeView

    day = date(2026, 7, 31)
    _write(config.curated_root / "daily_bars", f"trade_date={day}", [_row("600519.SH", day)])
    view = LakeView(config)

    assert view.health()["rows"] == 0
    rows = {r["dataset"]: r for r in view.datasets()}
    assert rows["daily_bars"]["has_data"] is True
    assert rows["daily_bars"]["row_count"] is None
    assert view.provenance("daily_bars") == []


def test_dates_serialise_as_plain_iso_days(client):
    body = client.get("/api/health").json()
    assert date.fromisoformat(body["anchor"])
