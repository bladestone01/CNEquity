"""What a ``trading_status`` row means, in one place.

The dataset used to carry two orthogonal facts in a single ``status`` string:
whether a security was **trading** that day, and whether it carried the
exchange's **risk-warning** designation (ST / *ST). One column cannot hold two
independent facts, and the writer resolved the conflict with an ``if/elif`` that
let suspension win:

    000711.SZ (ST京蓝)  2026-08-27  status=st
                        2026-08-28  status=suspended

The company did not leave risk warning that day — it halted. The stored history
lost the designation anyway, and every consumer that asked "was this ST" got the
wrong answer for the halt. `market_breadth` in particular reads it to pick the
±5% limit band, so a halted ST name was priced with the ±10% band.

So the two facts are now two columns:

* ``status`` — trading state: ``normal`` | ``suspended`` | ``delisted``
* ``risk_warning`` — the ST / *ST designation, independent of the above

``delisted`` is the second half of the same problem. The daily writer classified
everything that was neither halted nor on the ST board as ``normal`` with
``is_trading=True``, with no notion of delisting — so 611 symbols carrying a
``delist_date`` (one of them since 1999) were published as normally trading
every session. A dataset answering "was this security trading on day X" has to
be able to say "no, it was gone".

**ST vs *ST is not distinguished here.** No source that feeds this dataset ever
made the distinction — Baostock exposes a single ``isST`` flag, and the Tushare
adapter already collapsed its ``ST``/``*ST`` type to one value — so a boolean is
what the evidence actually supports. The finer designation lives in the
exchange 简称, reachable through ``instruments.name`` and
``adapters.exchange.st_lists.is_st_name``.

**Reading a lake that predates the split.** Old rows encode ST as
``status="st"``. :func:`risk_warning_expr` accepts both encodings, so queries
are correct before and after ``scripts/migrate_trading_status_risk_warning.py``
runs; the migration only makes the old rows say it in the new column.
"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

STATUS_NORMAL = "normal"
STATUS_SUSPENDED = "suspended"
STATUS_DELISTED = "delisted"

#: Trading states a row may carry. ``status`` no longer holds ST.
TRADING_STATES = frozenset({STATUS_NORMAL, STATUS_SUSPENDED, STATUS_DELISTED})

#: States in which the security was not trading.
NON_TRADING_STATES = frozenset({STATUS_SUSPENDED, STATUS_DELISTED})

#: How ST was encoded before ``risk_warning`` existed. Read-side only: nothing
#: writes these any more, and the migration rewrites them.
LEGACY_ST_STATUSES = frozenset({"st", "*st"})

#: Provenance for rows the lake derives from `instruments` rather than reading
#: from a vendor board: a delisted security appears on no daily board, so no
#: snapshot can say it stopped trading.
DELISTED_SOURCE = "derived_delisted"


def risk_warning_expr(columns: Iterable[str]) -> pl.Expr:
    """Whether a row carries the ST / *ST designation, in either encoding.

    Takes the frame's column names because a lake mid-migration has files both
    with and without ``risk_warning``; the legacy ``status`` encoding is always
    consulted so a partition that has not been rewritten still answers
    correctly rather than silently reporting every old ST day as clean.
    """
    legacy = pl.col("status").is_in(list(LEGACY_ST_STATUSES))
    if "risk_warning" in set(columns):
        return pl.col("risk_warning").fill_null(False) | legacy
    return legacy


def not_trading_expr(columns: Iterable[str]) -> pl.Expr:
    """Whether a row says the security was not trading that day."""
    expr = pl.col("status").is_in(list(NON_TRADING_STATES))
    if "is_trading" in set(columns):
        expr = expr | ~pl.col("is_trading").fill_null(False)
    return expr


def normalize_legacy(df: pl.DataFrame) -> pl.DataFrame:
    """Bring a frame onto the two-column encoding. Idempotent.

    A lake written before the split stores ST as ``status="st"`` and has no
    ``risk_warning`` column at all, which is a hard read error for anything
    that validates against the current schema. Rather than loosening that
    validation — which would let a genuinely malformed frame through — every
    read of stored ``trading_status`` passes through here, and any partition
    that gets rewritten afterwards is migrated as a side effect.
    """
    if "status" not in df.columns:
        return df
    return df.with_columns(
        risk_warning_expr(df.columns).alias("risk_warning"),
        pl.when(pl.col("status").is_in(list(LEGACY_ST_STATUSES)))
        .then(pl.lit(STATUS_NORMAL))
        .otherwise(pl.col("status"))
        .alias("status"),
    )


def is_risk_warning(status: str | None, risk_warning: bool | None = None) -> bool:
    """Row-wise form of :func:`risk_warning_expr` for non-polars callers."""
    if risk_warning:
        return True
    return str(status or "").strip().lower() in LEGACY_ST_STATUSES
