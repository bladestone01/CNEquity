"""`asl serve` — the read-only lake dashboard."""

from ashare_lake.serve.app import create_app
from ashare_lake.serve.lake import LakeView

__all__ = ["LakeView", "create_app"]
