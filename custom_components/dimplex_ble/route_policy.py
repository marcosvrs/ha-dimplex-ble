"""Pure route ranking policy shared by pairing and runtime reconnects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    """Normalized facts used to compare one Bluetooth proxy route."""

    source: str
    rssi: int
    free_slots: int | None = None
    assigned_heaters: int = 0

    @property
    def has_capacity(self) -> bool:
        """Return whether the route can accept another GATT connection."""
        return self.free_slots is None or self.free_slots > 0

    @property
    def allocation_known(self) -> bool:
        """Return whether the proxy reported an allocation count."""
        return self.free_slots is not None


def source_key(source: str) -> str:
    """Normalize a Bluetooth scanner source for identity comparisons."""
    return source.casefold()


def rank_route(
    candidate: RouteCandidate,
    *,
    active_source: str | None = None,
    prefer_active: bool = False,
) -> tuple[int, int, int, int, int]:
    """Return the deterministic ranking key for a usable route."""
    if not candidate.has_capacity:
        return (-1, 0, 0, 0, -127)

    return (
        int(
            prefer_active
            and active_source is not None
            and source_key(candidate.source) == source_key(active_source)
        ),
        int(candidate.allocation_known),
        candidate.free_slots or 0,
        -candidate.assigned_heaters,
        candidate.rssi,
    )


def rank_routes(
    candidates: tuple[RouteCandidate, ...] | list[RouteCandidate],
    *,
    active_source: str | None = None,
    prefer_active: bool = False,
) -> list[RouteCandidate]:
    """Return usable routes from best to worst using one shared policy."""
    return sorted(
        (candidate for candidate in candidates if candidate.has_capacity),
        key=lambda candidate: rank_route(
            candidate,
            active_source=active_source,
            prefer_active=prefer_active,
        ),
        reverse=True,
    )
