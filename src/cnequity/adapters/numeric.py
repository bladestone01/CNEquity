"""Numeric parsing helpers shared by source adapters."""

from __future__ import annotations

import math


def finite_int64(
    value: float,
    *,
    minimum: int = -(2**63),
    maximum: int = 2**63 - 1,
) -> int:
    """Convert a finite integral float within the signed Int64 range."""
    if not math.isfinite(value) or not value.is_integer():
        raise ValueError(f"expected a finite integer, got {value!r}")
    if value < minimum or value > maximum:
        raise ValueError(f"integer outside Int64 range: {value!r}")
    return int(value)
