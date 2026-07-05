"""Per-dataset incremental watermarks under meta/state/."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path


class StateStore:
    """Tracks last-success coverage per dataset (e.g. last trade_date)."""

    def __init__(self, meta_root: Path):
        self.root = meta_root / "state"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, dataset: str) -> Path:
        return self.root / f"{dataset}.json"

    def get_date(self, dataset: str, field: str = "last_success_trade_date") -> date | None:
        path = self._path(dataset)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        value = raw.get(field)
        if not value:
            return None
        return date.fromisoformat(str(value))

    def set_date(
        self,
        dataset: str,
        value: date,
        *,
        field: str = "last_success_trade_date",
    ) -> None:
        path = self._path(dataset)
        payload = {
            field: value.isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if path.exists():
            payload = {**json.loads(path.read_text(encoding="utf-8")), **payload}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def update_max_date(
        self,
        dataset: str,
        candidate: date,
        *,
        field: str = "last_success_trade_date",
    ) -> None:
        current = self.get_date(dataset, field=field)
        if current is None or candidate > current:
            self.set_date(dataset, candidate, field=field)
