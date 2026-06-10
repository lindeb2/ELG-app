"""Streak projection shared by commit and rebuild paths."""
from __future__ import annotations


def project_streak(current: int, *, active_inc: int, prior_period_active: bool) -> int:
    """Compute new running streak after a log commit."""
    if active_inc == 0:
        return current
    return current + 1 if prior_period_active else 1


def ctx_bool(value) -> bool:
    """Normalize MongoDB truthy (1/0, bool) from prefetch context."""
    return bool(value)
