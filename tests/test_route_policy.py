from __future__ import annotations

from custom_components.dimplex_ble.route_policy import (
    RouteCandidate,
    rank_route,
    rank_routes,
)


def test_rank_routes_prefers_capacity_over_rssi() -> None:
    routes = rank_routes(
        [
            RouteCandidate("full", -20, free_slots=0),
            RouteCandidate("balanced", -80, free_slots=2),
        ]
    )

    assert [route.source for route in routes] == ["balanced"]


def test_rank_routes_prefers_active_route_after_capacity_is_known() -> None:
    routes = rank_routes(
        [
            RouteCandidate("strong", -30, free_slots=1),
            RouteCandidate("active", -70, free_slots=1),
        ],
        active_source="active",
        prefer_active=True,
    )

    assert [route.source for route in routes] == ["active", "strong"]


def test_rank_route_does_not_prefer_active_by_default() -> None:
    routes = rank_routes(
        [
            RouteCandidate("strong", -30, free_slots=1),
            RouteCandidate("active", -70, free_slots=1),
        ],
        active_source="active",
    )

    assert [route.source for route in routes] == ["strong", "active"]


def test_rank_route_reports_unknown_capacity_as_zero_in_sort_key() -> None:
    assert rank_route(RouteCandidate("unknown", -20, free_slots=None)) == (
        0,
        0,
        0,
        0,
        -20,
    )


def test_rank_route_default_does_not_prefer_active() -> None:
    assert rank_route(
        RouteCandidate("strong", -30, free_slots=1),
        active_source="active",
    ) > rank_route(
        RouteCandidate("active", -70, free_slots=1),
        active_source="active",
    )


def test_rank_routes_uses_assignment_load_before_rssi() -> None:
    routes = rank_routes(
        [
            RouteCandidate("busy", -30, free_slots=None, assigned_heaters=2),
            RouteCandidate("free", -80, free_slots=None, assigned_heaters=0),
        ]
    )

    assert [route.source for route in routes] == ["free", "busy"]


def test_rank_route_marks_full_route_unusable() -> None:
    assert rank_route(RouteCandidate("full", -20, free_slots=0)) == (
        -1,
        0,
        0,
        0,
        -127,
    )
