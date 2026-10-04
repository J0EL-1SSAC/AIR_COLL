"""Validation shared by REST date filters and replay range requests."""
from __future__ import annotations


def validate_time_range(start: float | None, end: float | None, *, now: float,
                        earliest: float | None = None) -> str | None:
    if start is not None and end is not None and start > end:
        return "From time must be at or before To time."
    if start is not None and start > now:
        return "From time cannot be in the future."
    if end is not None and end > now:
        return "To time cannot be in the future."
    if earliest is not None and start is not None and start < earliest:
        return "From time is earlier than the recorded data range."
    return None
