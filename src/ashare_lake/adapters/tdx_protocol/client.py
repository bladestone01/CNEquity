from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import as_completed as _as_completed
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ashare_lake.adapters.calendar.exchange_calendar import (
    build_trading_calendar,
    ensure_seed_csv,
)
from ashare_lake.adapters.eastmoney.corporate_actions import fetch_corporate_actions_eastmoney
from ashare_lake.adapters.eastmoney.trading_status import fetch_trading_status_eastmoney
from ashare_lake.adapters.tdx_protocol.bars import fetch_bars_paginated
from ashare_lake.adapters.tdx_protocol.corporate_actions import fetch_corporate_actions_tdx
from ashare_lake.adapters.tdx_protocol.session import TDX_SESSION_LOCK, close_quotes_client
from ashare_lake.config import Config
from ashare_lake.domain.rate_limit import RateLimitSpec, wait_spec
from ashare_lake.domain.schemas import MOCK_SOURCE, with_provenance
from ashare_lake.domain.symbols import (
    ETF_PREFIXES,
    PREFIX_WHITELIST,
    format_symbol,
    is_cdr_symbol,
    is_etf_symbol,
)

logger = logging.getLogger(__name__)

_close_quotes_client = close_quotes_client

INDEX_SYMBOLS = [
    ("000001", "SH"),
    ("399001", "SZ"),
    ("399006", "SZ"),
    ("000688", "SH"),
    ("000016", "SH"),
    ("000300", "SH"),
    ("000905", "SH"),
    ("000852", "SH"),
]


class TdxSourceError(RuntimeError):
    """Raised when the TDX source cannot deliver real data.

    Fabricated data is only allowed behind an explicit `allow_mock=True`
    (config `[tdx_protocol].allow_mock`), which skips the network entirely —
    an upstream bestip scan can block indefinitely offline — and returns rows
    labeled `source="mock"` so audit can reject them.
    """


# A validated (host, port) reused across fetches in this process. An upstream
# bestip scan is slow (~75s) and intermittently selects a server that then
# fails the actual fetch. Worse, some bundled hosts are TCP-reachable but
# return zero rows for every symbol (dead data feed), so we validate a
# candidate by actually fetching a known bar before trusting it.
_TDX_SERVER_CACHE: tuple[str, int] | None = None
_TDX_TCP_TIMEOUT = 1.5
_TDX_PROBE_SYMBOL = "000001"  # SSE composite; market=1
_TDX_MAX_CANDIDATES = 16
_TDX_PROBE_CONCURRENCY = 8  # parallel probes; first live responder wins
_TDX_FETCH_ATTEMPTS = 3  # server rotations before a bar fetch fails loud


def reset_tdx_server_cache() -> None:
    """Forget the cached TDX server so the next client re-probes (on failure)."""
    global _TDX_SERVER_CACHE
    _TDX_SERVER_CACHE = None


def _reachable(host: str, port: int, timeout: float = _TDX_TCP_TIMEOUT) -> bool:
    import socket

    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _serves_data(host: str, port: int, timeout: int) -> bool:
    """A server passes only if it returns a real bar — filters dead feeds.

    Uses ``heartbeat=False`` so the throwaway probe leaves no lingering thread.
    """
    from ashare_lake.adapters.tdx_protocol.quotes import Quotes

    client = None
    try:
        client = Quotes.factory(server=(host, int(port)), timeout=timeout, heartbeat=False)
        rows = client.bars(_TDX_PROBE_SYMBOL, market=1, start=0, offset=1)
        return bool(rows)
    except Exception:
        return False
    finally:
        if client is not None:
            client.close()


def _candidate_servers(config: Config | None) -> list[tuple[str, int]]:
    """Configured host pool first (in order), then the bundled fallback hosts."""
    import random

    from ashare_lake.adapters.tdx_protocol.hosts import HQ_HOSTS

    ordered: list[tuple[str, int]] = []
    if config is not None and config.tdx_host_pool:
        for entry in config.tdx_host_pool:
            host, _, port = entry.rpartition(":")
            if host and port.isdigit():
                ordered.append((host, int(port)))

    bundled = [(host, int(port)) for host, port in HQ_HOSTS]
    random.shuffle(bundled)  # spread load across the fallback list
    ordered.extend(bundled)

    seen: set[tuple[str, int]] = set()
    out: list[tuple[str, int]] = []
    for hp in ordered:
        if hp not in seen:
            seen.add(hp)
            out.append(hp)
    return out


def _probe(host: str, port: int, timeout: int) -> bool:
    return _reachable(host, port) and _serves_data(host, port, timeout)


def _pick_reachable_server(config: Config | None = None, timeout: int = 10) -> tuple[str, int]:
    """Probe candidates in parallel; return the first that serves real data.

    Parallel probing means the first future to resolve true is effectively the
    lowest-latency live server, so selection is both fast and fastest-first.
    """
    from concurrent.futures import ThreadPoolExecutor

    candidates = _candidate_servers(config)[:_TDX_MAX_CANDIDATES]
    if not candidates:
        raise TdxSourceError("no TDX candidate servers configured or bundled")

    with ThreadPoolExecutor(max_workers=min(len(candidates), _TDX_PROBE_CONCURRENCY)) as pool:
        futures = {pool.submit(_probe, h, p, timeout): (h, p) for h, p in candidates}
        try:
            for fut in _as_completed(futures):
                if fut.result():
                    return futures[fut]
        finally:
            for fut in futures:
                fut.cancel()
    raise TdxSourceError(
        f"no TDX server responded with data (probed {len(candidates)} host(s); "
        "network down or all feeds degraded)"
    )


def _quotes_client(config: Config | None = None):
    """Build a TDX client bound to a reachable, cached server.

    Isolated so tests can monkeypatch it.
    """
    global _TDX_SERVER_CACHE
    from ashare_lake.adapters.tdx_protocol.quotes import Quotes

    timeout = config.tdx_connect_timeout_sec if config else 10
    servers = (config.tdx_servers if config else "auto").strip()
    kwargs: dict[str, object] = {
        "multithread": True,
        "heartbeat": True,
        "timeout": timeout,
    }
    if servers.lower() == "auto":
        if _TDX_SERVER_CACHE is None:
            _TDX_SERVER_CACHE = _pick_reachable_server(config, timeout=timeout)
        kwargs["server"] = _TDX_SERVER_CACHE
    else:
        host, sep, port = servers.partition(":")
        if not sep:
            raise TdxSourceError(
                f"invalid [tdx_protocol].servers {servers!r}; use 'auto' or host:port"
            )
        kwargs["server"] = (host.strip(), int(port.strip()))
    return Quotes.factory(**kwargs)


def quotes_client_factory(config: Config | None = None):
    """Callable factory for corporate_actions xdxr (one client per batch)."""
    return lambda: _quotes_client(config)


# TDX market ids: 0=Shenzhen, 1=Shanghai (not "SH"/"SZ" strings).
_TDX_STOCK_MARKETS = ((1, "SH"), (0, "SZ"))


def _asset_type_for(code: str, exch: str) -> str:
    if is_cdr_symbol(code, exch):
        return "cdr"
    if is_etf_symbol(code, exch):
        return "etf"
    return "stock"


def _filter_instrument_frame(pdf: pl.DataFrame, exch: str) -> pl.DataFrame:
    from ashare_lake.domain.symbols import is_subscription_placeholder

    code_col = "code" if "code" in pdf.columns else pdf.columns[0]
    name_col = "name" if "name" in pdf.columns else pdf.columns[1]
    codes = pdf[code_col].cast(pl.Utf8).str.zfill(6)
    prefixes = PREFIX_WHITELIST.get(exch.upper(), ()) + ETF_PREFIXES.get(exch.upper(), ())
    mask = pl.lit(False)
    for prefix in prefixes:
        mask = mask | codes.str.starts_with(prefix)
    for blocked in range(81, 90):
        mask = mask & ~codes.str.starts_with(str(blocked))
    filtered = pdf.filter(mask)
    if filtered.is_empty():
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "name": pl.Utf8,
                "exchange": pl.Utf8,
                "asset_type": pl.Utf8,
                "list_date": pl.Date,
                "delist_date": pl.Date,
                "prev_symbol": pl.Utf8,
            }
        )
    rows = []
    for row in filtered.iter_rows(named=True):
        code = str(row[code_col]).zfill(6)
        name = str(row[name_col])
        if is_subscription_placeholder(name):
            continue
        rows.append(
            {
                "symbol": format_symbol(code, exch),
                "name": name,
                "exchange": exch,
                "asset_type": _asset_type_for(code, exch),
                "list_date": None,
                "delist_date": None,
                "prev_symbol": None,
            }
        )
    if not rows:
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "name": pl.Utf8,
                "exchange": pl.Utf8,
                "asset_type": pl.Utf8,
                "list_date": pl.Date,
                "delist_date": pl.Date,
                "prev_symbol": pl.Utf8,
            }
        )
    return pl.DataFrame(rows)


def _mark_mock(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(pl.lit(MOCK_SOURCE).alias("source"))


def _mock_instruments() -> pl.DataFrame:
    rows = []
    for code, exch in [("600519", "SH"), ("000001", "SZ"), ("300750", "SZ"), ("920000", "BJ")]:
        rows.append(
            {
                "symbol": format_symbol(code, exch),
                "name": f"Mock-{code}",
                "exchange": exch,
                "asset_type": "stock",
                "list_date": date(2010, 1, 1),
                "delist_date": None,
                "prev_symbol": None,
            }
        )
    return _mark_mock(pl.DataFrame(rows))


def _mock_calendar(start: date, end: date) -> pl.DataFrame:
    rows = []
    d = start
    while d <= end:
        is_trading = d.weekday() < 5
        rows.append({"trade_date": d, "is_trading": is_trading})
        d += timedelta(days=1)
    return _mark_mock(pl.DataFrame(rows))


def _mock_bars(symbols: list[str], start: date, end: date) -> pl.DataFrame:
    rows = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            for i, sym in enumerate(symbols):
                base = 10.0 + i
                rows.append(
                    {
                        "symbol": sym,
                        "trade_date": d,
                        "open": base,
                        "high": base + 1,
                        "low": base - 0.5,
                        "close": base + 0.2,
                        "volume": 1_000_000,
                        "amount": base * 1_000_000,
                    }
                )
        d += timedelta(days=1)
    return _mark_mock(pl.DataFrame(rows))


def _fail_or_mock(
    dataset: str, reason: str, allow_mock: bool, mock_df: pl.DataFrame
) -> pl.DataFrame:
    if not allow_mock:
        raise TdxSourceError(f"{dataset}: {reason} (set [tdx_protocol].allow_mock for tests)")
    logger.warning("%s: %s; returning mock rows labeled source=%s", dataset, reason, MOCK_SOURCE)
    return mock_df


_MOCK_SHORT_CIRCUIT = "allow_mock enabled; skipping network fetch"


def fetch_instruments(
    *,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
    config: Config | None = None,
) -> pl.DataFrame:
    if allow_mock:
        return _fail_or_mock("instruments", _MOCK_SHORT_CIRCUIT, True, _mock_instruments())
    wait_spec(rate_limit)
    client = None
    try:
        with TDX_SESSION_LOCK:
            client = _quotes_client(config)
            frames = []
            market_errors: list[str] = []
            for market, exch in _TDX_STOCK_MARKETS:
                try:
                    raw = client.stocks(market=market)
                except Exception as exc:
                    market_errors.append(f"{exch}: {exc}")
                    continue
                if raw is None or len(raw) == 0:
                    market_errors.append(f"{exch}: empty response")
                    continue
                pdf = pl.from_pandas(raw) if hasattr(raw, "columns") else pl.DataFrame(raw)
                part = _filter_instrument_frame(pdf, exch)
                if part.height:
                    frames.append(part)
                else:
                    market_errors.append(f"{exch}: no qualifying instruments")
            if market_errors:
                reason = "market fetch failed: " + "; ".join(market_errors)
                return _fail_or_mock("instruments", reason, allow_mock, _mock_instruments())
            if not frames:
                reason = "TDX returned no instruments"
                return _fail_or_mock("instruments", reason, allow_mock, _mock_instruments())
            return pl.concat(frames, how="diagonal_relaxed")
    except ImportError:
        reason = "TDX wire client unavailable"
    except Exception as exc:
        # Drop the cached server so the next attempt (batch retry) re-probes
        # for a live one instead of hammering the same dead host.
        reset_tdx_server_cache()
        reason = f"TDX fetch failed: {exc}"
    finally:
        _close_quotes_client(client)
    return _fail_or_mock("instruments", reason, allow_mock, _mock_instruments())


def fetch_trading_calendar(
    start: date,
    end: date,
    *,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
    curated_root: Path | None = None,
    seed_path: Path | None = None,
) -> pl.DataFrame:
    wait_spec(rate_limit)
    try:
        ensure_seed_csv(seed_path)
        return build_trading_calendar(
            start,
            end,
            seed_path=seed_path,
            curated_root=curated_root,
        )
    except Exception as exc:
        reason = f"calendar seed load failed: {exc}"
        return _fail_or_mock("trading_calendar", reason, allow_mock, _mock_calendar(start, end))


def fetch_daily_bars(
    symbols: list[str],
    start: date,
    end: date,
    *,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
    backfill: bool = False,
    config: Config | None = None,
    on_heartbeat: Callable[[], None] | None = None,
) -> pl.DataFrame:
    if allow_mock:
        return _fail_or_mock(
            "daily_bars", _MOCK_SHORT_CIRCUIT, True, _mock_bars(symbols, start, end)
        )
    client = None
    try:
        with TDX_SESSION_LOCK:
            client = _quotes_client(config)
            rows = []
            for sym in symbols:
                if on_heartbeat is not None:
                    on_heartbeat()
                rows.extend(
                    fetch_bars_paginated(
                        client,
                        sym,
                        start,
                        end,
                        rate_limit=rate_limit,
                        backfill=backfill,
                        on_page=on_heartbeat,
                    )
                )
            if rows:
                return pl.DataFrame(rows)
            reason = "TDX returned no bars"
    except ImportError:
        reason = "TDX wire client unavailable"
    except Exception as exc:
        # Drop the cached server so the next attempt (batch retry) re-probes
        # for a live one instead of hammering the same dead host.
        reset_tdx_server_cache()
        reason = f"TDX fetch failed: {exc}"
    finally:
        _close_quotes_client(client)
    return _fail_or_mock("daily_bars", reason, allow_mock, _mock_bars(symbols, start, end))


def fetch_index_bars(
    start: date,
    end: date,
    *,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
    backfill: bool = False,
    config: Config | None = None,
) -> pl.DataFrame:
    symbols = [format_symbol(c, e) for c, e in INDEX_SYMBOLS]
    if allow_mock:
        return _fail_or_mock(
            "index_bars",
            _MOCK_SHORT_CIRCUIT,
            True,
            _mock_bars(symbols, start, end).with_columns(pl.lit("1d").alias("frequency")),
        )

    def _fetch_once() -> tuple[list[dict], list[str]]:
        with TDX_SESSION_LOCK:
            client = _quotes_client(config)
            rows: list[dict] = []
            missing: list[str] = []
            try:
                for sym in symbols:
                    try:
                        sym_rows = fetch_bars_paginated(
                            client,
                            sym,
                            start,
                            end,
                            rate_limit=rate_limit,
                            backfill=backfill,
                            is_index=True,
                        )
                    except Exception as exc:
                        if backfill:
                            # Rotate server and retry the whole set — some TDX hosts
                            # return corrupt bytes for deep index history.
                            raise TdxSourceError(f"index bars failed for {sym}: {exc}") from exc
                        # Daily mode: treat hard failures as missing so a partial
                        # set cannot advance the watermark (lake previously kept
                        # only 000852.SH on some days while other indices failed).
                        logger.warning("TDX index bars failed for %s: %s", sym, exc)
                        missing.append(sym)
                        continue
                    if not sym_rows:
                        missing.append(sym)
                        continue
                    rows.extend(sym_rows)
            finally:
                _close_quotes_client(client)
            return rows, missing

    reason = "TDX returned no index bars"
    try:
        last_exc: Exception | None = None
        for attempt in range(_TDX_FETCH_ATTEMPTS):
            try:
                rows, missing = _fetch_once()
                # Fail-loud on any incomplete symbol set — both backfill and daily.
                # Accepting a non-empty subset used to leave curated partitions with
                # only one index while audit reported calendar coverage gaps.
                if missing:
                    raise TdxSourceError("index bars returned no rows for: " + ", ".join(missing))
                if rows:
                    return pl.DataFrame(rows).with_columns(pl.lit("1d").alias("frequency"))
                break
            except TdxSourceError as exc:
                last_exc = exc
                reset_tdx_server_cache()
                logger.warning(
                    "index bars attempt %d/%d failed: %s; rotating server",
                    attempt + 1,
                    _TDX_FETCH_ATTEMPTS,
                    exc,
                )
        if last_exc is not None:
            reason = f"TDX fetch failed: {last_exc}"
    except ImportError:
        reason = "TDX wire client unavailable"
    except Exception as exc:
        reset_tdx_server_cache()
        reason = f"TDX fetch failed: {exc}"
    return _fail_or_mock(
        "index_bars",
        reason,
        allow_mock,
        _mock_bars(symbols, start, end).with_columns(pl.lit("1d").alias("frequency")),
    )


def fetch_corporate_actions(
    trade_date: date,
    *,
    symbols: list[str] | None = None,
    backfill: bool = False,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
    primary_only: bool = False,
    config: Config | None = None,
) -> pl.DataFrame:
    empty = pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "ex_date": pl.Date,
            "action_type": pl.Utf8,
            "cash_dividend": pl.Float64,
            "bonus_ratio": pl.Float64,
            "transfer_ratio": pl.Float64,
            "allotment_ratio": pl.Float64,
            "allotment_price": pl.Float64,
        }
    )
    if allow_mock:
        return _fail_or_mock("corporate_actions", _MOCK_SHORT_CIRCUIT, True, empty)
    wait_spec(rate_limit)

    frames: list[pl.DataFrame] = []
    try:
        if symbols:
            tdx_df = fetch_corporate_actions_tdx(
                symbols,
                trade_date=trade_date,
                backfill=backfill,
                client_factory=quotes_client_factory(config),
                rate_limit=rate_limit,
            )
            if tdx_df.height:
                frames.append(tdx_df.with_columns(pl.lit("tdx_protocol").alias("source")))
    except ImportError:
        logger.debug("TDX wire client unavailable for corporate_actions")
    except Exception as exc:
        logger.warning("TDX corporate_actions failed: %s", exc)

    try:
        if not primary_only:
            em_df = fetch_corporate_actions_eastmoney(trade_date, backfill=backfill)
            if em_df.height:
                frames.append(em_df.with_columns(pl.lit("eastmoney").alias("source")))
    except Exception as exc:
        logger.warning("EastMoney corporate_actions backup failed: %s", exc)

    if frames:
        out = pl.concat(frames, how="diagonal_relaxed")
        if "source" not in out.columns:
            out = out.with_columns(pl.lit("tdx_protocol").alias("source"))
        else:
            out = out.with_columns(
                pl.when(pl.col("source").is_null())
                .then(pl.lit("tdx_protocol"))
                .otherwise(pl.col("source"))
                .alias("source")
            )
        if not backfill:
            out = out.filter(pl.col("ex_date") == trade_date)
        return out.unique(subset=["symbol", "ex_date", "action_type"], keep="last")

    return _fail_or_mock(
        "corporate_actions",
        "no corporate actions from TDX or EastMoney",
        allow_mock,
        empty,
    )


def fetch_trading_status(
    symbols: list[str],
    trade_date: date,
    *,
    rate_limit: RateLimitSpec | None = None,
    allow_mock: bool = False,
    extra_st_symbols: set[str] | None = None,
) -> pl.DataFrame:
    def _mock_status() -> pl.DataFrame:
        rows = [
            {
                "symbol": sym,
                "trade_date": trade_date,
                "is_trading": True,
                "status": "normal",
            }
            for sym in symbols
        ]
        return _mark_mock(pl.DataFrame(rows))

    if allow_mock:
        return _fail_or_mock("trading_status", _MOCK_SHORT_CIRCUIT, True, _mock_status())

    wait_spec(rate_limit)
    try:
        df = fetch_trading_status_eastmoney(symbols, trade_date, extra_st_symbols=extra_st_symbols)
        if df.height:
            return df
        reason = "EastMoney returned no trading status rows"
    except Exception as exc:
        reason = f"EastMoney trading_status failed: {exc}"

    return _fail_or_mock(
        "trading_status",
        reason,
        allow_mock,
        _mock_status(),
    )


def normalize_with_source(df: pl.DataFrame, source: str = "tdx_protocol") -> pl.DataFrame:
    return with_provenance(df, source=source, data_version="v1")
