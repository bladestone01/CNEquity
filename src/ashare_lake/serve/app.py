"""The read-only lake dashboard: JSON API plus one self-contained page.

**Nothing here writes to the lake.** There is no endpoint that runs, retries or
cleans anything, and there will not be: an unauthenticated local HTTP service
that can trigger ingestion is a liability, and the CLI is already the right
front door for those. The page shows the command to run and lets you copy it.

The one exception proves the rule — ``meta/stats`` is regenerated in the
background when ingestion has moved on, because it is a cache of the lake rather
than part of it, and a dashboard serving numbers from last week is worse than
one that refreshes its own cache.

Responses are pydantic models so ``/api/docs`` documents the real contract:
the OpenAPI page is generated from the handlers and cannot drift from them.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ashare_lake.config import Config
from ashare_lake.serve.lake import LakeView

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class Health(BaseModel):
    anchor: date = Field(description="Last trading day; freshness is judged against this.")
    datasets: int
    fresh: int
    stale: int
    empty: int
    not_applicable: int
    stale_datasets: list[str]
    empty_optional: list[str] = Field(description="Empty and required=False — expected, not a gap.")
    empty_required: list[str] = Field(description="Empty and required — a real gap.")
    rows: int
    bytes: int
    findings_by_severity: dict[str, int]
    audit_trade_date: str | None
    stats_stale: bool
    stats_reason: str | None
    stats_generated_at: datetime | None


class Tier(BaseModel):
    tier: str
    label: str
    datasets: int
    fresh: int
    stale: int
    empty: int
    rows: int
    bytes: int
    members: list[str]


class Dataset(BaseModel):
    dataset: str
    tier: str
    tier_label: str
    layer: str
    granularity: str | None
    date_col: str | None
    fetch_semantics: str
    history_mode: str
    backfill_source: str | None
    history_horizon_days: int | None
    pit: bool
    required: bool
    intraday: str | None
    has_data: bool
    coverage_start: date | None
    coverage_end: date | None
    watermarked: bool
    watermark: date | None
    freshness: str
    row_count: int | None
    bytes: int | None
    partitions: int | None


class Column(BaseModel):
    column: str
    dtype: str


class PartitionStat(BaseModel):
    partition: str | None
    granularity: str | None
    period_start: date | None
    period_end: date | None
    row_count: int
    bytes: int


class Gaps(BaseModel):
    missing: list[str] = Field(description="Missing partition values, capped at 60.")
    total: int
    unit: str = Field(description="Counted in the dataset's own period, not in days.")


class Command(BaseModel):
    cmd: str
    why: str


class Batch(BaseModel):
    run_id: str
    batch_id: str
    status: str
    window_start: str | None
    window_end: str | None
    rows_written: int | None
    retry_count: int | None
    started_at: str | None
    finished_at: str | None
    error_message: str | None


class DatasetDetail(Dataset):
    partition_col: str | None
    max_staleness_days: int
    backfill_chunk_days: int | None
    backfill_chunk_symbols: int | None
    earliest_available: date | None = Field(
        description="Source floor, not this lake's backlog: earlier windows return nothing."
    )
    primary_key: list[str]
    schema_columns: list[Column] = Field(alias="schema")
    gaps: Gaps
    findings: list[dict]
    commands: list[Command]
    batches: list[Batch]


class Provenance(BaseModel):
    source: str
    data_version: str
    row_count: int
    fetched_at_min: datetime | None
    fetched_at_max: datetime | None


class ProvenancePoint(BaseModel):
    """One (period, source, data_version) — the source mix as it moved."""

    period_start: date
    source: str
    data_version: str
    row_count: int


class ProvenanceSeries(BaseModel):
    bucket: str = Field(description="Width each point spans: day, month or year.")
    points: list[ProvenancePoint]


class HeatmapRow(BaseModel):
    dataset: str
    tier: str
    granularity: str | None
    freshness: str
    cadence_days: int = Field(
        description="Days this dataset may lag before it counts as stale. "
        "Above 1 the source is not daily, so gaps are its cadence, not a fault."
    )
    cells: str = Field(description="One char per day; see `legend`.")


class Heatmap(BaseModel):
    days: list[date]
    legend: dict[str, str]
    rows: list[HeatmapRow]


def get_view(request: Request) -> LakeView:
    return request.app.state.view


# Annotated rather than a `= Depends(...)` default: the same wiring, but the call
# stays out of the signature's defaults, where it is both a bugbear finding
# (B008) and evaluated once at import.
View = Annotated[LakeView, Depends(get_view)]


def create_app(config: Config, *, token: str | None = None) -> FastAPI:
    """Build the dashboard app for *config*.

    *token*, when set, is required as ``Authorization: Bearer <token>`` or
    ``?token=``. The CLI makes it mandatory for a non-loopback bind — this
    service has no other access control and should not be reachable without one.
    """
    app = FastAPI(
        title="ashare-lake dashboard",
        description="Read-only view of one lake: coverage, freshness and provenance.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.view = LakeView(config)
    app.state.token = token

    @app.middleware("http")
    async def _authenticate(request: Request, call_next):
        expected = request.app.state.token
        if expected:
            header = request.headers.get("authorization", "")
            supplied = header[7:] if header.lower().startswith("bearer ") else None
            supplied = supplied or request.query_params.get("token")
            if supplied != expected:
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        page = STATIC_DIR / "index.html"
        if not page.exists():  # pragma: no cover — packaging failure
            raise HTTPException(500, "dashboard page missing from the installed package")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @app.get("/api/health", response_model=Health)
    def health(view: View) -> Health:
        # The overview page loads this first, so it is where the cache gets its
        # chance to notice the lake moved. Returns immediately either way.
        view.refresh_stats_in_background()
        return Health(**view.health())

    @app.get("/api/tiers", response_model=list[Tier])
    def tiers(view: View) -> list[Tier]:
        return [Tier(**row) for row in view.tiers()]

    @app.get("/api/datasets", response_model=list[Dataset])
    def datasets(
        view: View,
        tier: Annotated[str | None, Query(description="Restrict to one L0–L8 tier.")] = None,
    ) -> list[Dataset]:
        return [Dataset(**row) for row in view.datasets(tier=tier)]

    def _known(dataset: str) -> None:
        from ashare_lake.domain.datasets import DATASETS

        if dataset not in DATASETS:
            raise HTTPException(404, f"unknown dataset {dataset!r}")

    @app.get("/api/datasets/{dataset}", response_model=DatasetDetail)
    def dataset_detail(dataset: str, view: View) -> DatasetDetail:
        _known(dataset)
        return DatasetDetail(**view.dataset_detail(dataset))

    @app.get("/api/datasets/{dataset}/partitions", response_model=list[PartitionStat])
    def dataset_partitions(dataset: str, view: View) -> list[PartitionStat]:
        _known(dataset)
        return [PartitionStat(**row) for row in view.partitions(dataset)]

    @app.get("/api/datasets/{dataset}/provenance", response_model=list[Provenance])
    def provenance(dataset: str, view: View) -> list[Provenance]:
        _known(dataset)
        return [Provenance(**row) for row in view.provenance(dataset)]

    @app.get("/api/datasets/{dataset}/provenance/series", response_model=ProvenanceSeries)
    def provenance_series(dataset: str, view: View) -> ProvenanceSeries:
        _known(dataset)
        return ProvenanceSeries(**view.provenance_series(dataset))

    @app.get("/api/heatmap", response_model=Heatmap)
    def heatmap(
        view: View,
        days: Annotated[
            int, Query(ge=1, le=750, description="Trading days back from the anchor.")
        ] = 90,
    ) -> Heatmap:
        return Heatmap(**view.heatmap(days=days))

    return app
