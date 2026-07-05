from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from stock_data_engine.domain.rate_limit import RateLimitSpec

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore


@dataclass
class WaveConfig:
    name: str
    parallel: bool
    steps: list[str]


@dataclass
class ScheduleGroup:
    at: str
    steps: list[str]


@dataclass
class Config:
    data_root: Path
    workers: int = 8
    batch_size: int = 100
    date_chunk_days: int = 30
    max_retries: int = 3
    retry_backoff_seconds: int = 5
    tdx_enabled: bool = True
    tdx_min_interval_ms: int = 50
    tdx_servers: str = "auto"
    tdx_connect_timeout_sec: int = 10
    # Test/demo escape hatch only: lets TDX adapters return fabricated rows
    # (labeled source="mock") instead of failing the batch.
    tdx_allow_mock: bool = False
    sources: dict[str, bool] = field(default_factory=dict)
    source_intervals: dict[str, float] = field(default_factory=dict)
    universe_default: str = "all_a"
    daily_waves: list[WaveConfig] = field(default_factory=list)
    schedule_groups: dict[str, ScheduleGroup] = field(default_factory=dict)
    init_phases: list[str] = field(default_factory=list)
    on_demand_enabled: bool = True
    on_demand_datasets: list[str] = field(default_factory=list)
    duckdb_path: Path | None = None
    duckdb_memory_limit: str = "2GB"
    duckdb_threads: int = 4
    adj_factors_source: str = "sina"
    adj_factors_types: list[str] = field(default_factory=lambda: ["qfq"])
    config_path: Path | None = None
    _backfill: bool = False
    _rate_limiters: object | None = field(default=None, repr=False)

    def rate_limit(self, source: str) -> None:
        if self._rate_limiters is None:
            from stock_data_engine.adapters.throttle import SourceRateLimiters

            self._rate_limiters = SourceRateLimiters(self)
        self._rate_limiters.wait(source)  # type: ignore[union-attr]

    def tdx_rate_limit_spec(self) -> RateLimitSpec | None:
        if not self.tdx_enabled:
            return None
        return RateLimitSpec(
            str(self.meta_root / "rate_limits"),
            "tdx_protocol",
            self.tdx_min_interval_ms / 1000.0,
        )

    @property
    def manifest_path(self) -> Path:
        return self.data_root / "meta" / "manifest.db"

    @property
    def staging_root(self) -> Path:
        return self.data_root / "staging"

    @property
    def curated_root(self) -> Path:
        return self.data_root / "curated"

    @property
    def derived_root(self) -> Path:
        return self.data_root / "derived"

    @property
    def meta_root(self) -> Path:
        return self.data_root / "meta"


def _expand(path_str: str, data_root: Path) -> Path:
    return Path(path_str.replace("{data.root}", str(data_root))).expanduser().resolve()


def load_config(path: str | Path) -> Config:
    config_path = Path(path).expanduser().resolve()
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    data_root = (
        Path(raw.get("data", {}).get("root", "./data/stock-data-engine")).expanduser().resolve()
    )
    orch = raw.get("orchestrator", {})
    tdx = raw.get("tdx_protocol", {})
    sources_raw = raw.get("sources", {})

    sources: dict[str, bool] = {}
    source_intervals: dict[str, float] = {}
    for name, val in sources_raw.items():
        if isinstance(val, dict):
            sources[name] = bool(val.get("enabled", True))
            if "min_interval_seconds" in val:
                source_intervals[name] = float(val["min_interval_seconds"])
        else:
            sources[name] = bool(val)

    daily_waves: list[WaveConfig] = []
    for wave in raw.get("job", {}).get("daily", {}).get("waves", []):
        daily_waves.append(
            WaveConfig(
                name=wave["name"],
                parallel=bool(wave.get("parallel", True)),
                steps=list(wave.get("steps", [])),
            )
        )

    schedule_groups: dict[str, ScheduleGroup] = {}
    groups_raw = raw.get("job", {}).get("daily", {}).get("groups", {})
    for name, group in groups_raw.items():
        schedule_groups[name] = ScheduleGroup(
            at=group.get("at", "16:00"), steps=list(group.get("steps", []))
        )

    duckdb_raw = raw.get("duckdb", {})
    duckdb_path_str = duckdb_raw.get("path")
    duckdb_path = (
        _expand(duckdb_path_str, data_root)
        if duckdb_path_str
        else data_root / "duckdb" / "stockdata.duckdb"
    )

    on_demand = raw.get("on_demand", {})
    adj_raw = raw.get("adj_factors", {})

    cfg = Config(
        data_root=data_root,
        workers=int(orch.get("workers", 8)),
        batch_size=int(orch.get("batch_size", 100)),
        date_chunk_days=int(orch.get("date_chunk_days", 30)),
        max_retries=int(orch.get("max_retries", 3)),
        retry_backoff_seconds=int(orch.get("retry_backoff_seconds", 5)),
        tdx_enabled=bool(tdx.get("enabled", True)),
        tdx_min_interval_ms=int(tdx.get("min_interval_ms", 50)),
        tdx_servers=str(tdx.get("servers", "auto")),
        tdx_connect_timeout_sec=int(tdx.get("connect_timeout_sec", 10)),
        tdx_allow_mock=bool(tdx.get("allow_mock", False)),
        sources=sources,
        source_intervals=source_intervals,
        universe_default=str(raw.get("universe", {}).get("default", "all_a")),
        daily_waves=daily_waves,
        schedule_groups=schedule_groups,
        init_phases=list(raw.get("job", {}).get("init", {}).get("names", [])),
        on_demand_enabled=bool(on_demand.get("enabled", True)),
        on_demand_datasets=list(on_demand.get("datasets", [])),
        duckdb_path=duckdb_path,
        duckdb_memory_limit=str(duckdb_raw.get("memory_limit", "2GB")),
        duckdb_threads=int(duckdb_raw.get("threads", 4)),
        adj_factors_source=str(adj_raw.get("source", "sina")),
        adj_factors_types=list(adj_raw.get("adjust_types", ["qfq"])),
        config_path=config_path,
    )
    return cfg


def validate_config(cfg: Config) -> list[str]:
    import stock_data_engine.steps  # noqa: F401 — register steps
    from stock_data_engine.orchestrator.registry import STEP_REGISTRY

    errors: list[str] = []
    if cfg.workers < 1:
        errors.append("orchestrator.workers must be >= 1")
    if cfg.batch_size < 1:
        errors.append("orchestrator.batch_size must be >= 1")
    if not cfg.daily_waves:
        errors.append("job.daily.waves must define at least one wave")

    referenced: list[tuple[str, str]] = []
    for wave in cfg.daily_waves:
        if not wave.steps:
            errors.append(f"wave '{wave.name}' has no steps")
        for step in wave.steps:
            referenced.append((f"wave '{wave.name}'", step))

    for group_name, group in cfg.schedule_groups.items():
        for step in group.steps:
            referenced.append((f"group '{group_name}'", step))

    for location, step in referenced:
        if step not in STEP_REGISTRY:
            errors.append(f"{location}: unknown step '{step}' (not registered)")

    return errors
