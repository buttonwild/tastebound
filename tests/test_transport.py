"""Journey option tests (ported from the original project, adapted to the Route schema)."""

import pytest

from tastebound.transport import choose_best_route, estimate_cost, get_routes, route_by_mode

WESTMINSTER = (51.5014, -0.1419)
BANK = (51.5079, -0.0877)
NEAR = (51.5033, -0.1195)
SHOREDITCH = (51.5155, -0.0722)
MANCHESTER = (53.4808, -2.2426)
OXFORD = (51.7520, -1.2577)
EDINBURGH = (55.9533, -3.1883)


def modes(routes):
    return {r.mode for r in routes}


def test_walk_only_for_very_short_trip():
    assert modes(get_routes(WESTMINSTER, (51.5020, -0.1410))) == {"walk"}


def test_walk_and_bus_for_short_trip():
    routes = get_routes(WESTMINSTER, BANK)
    assert modes(routes) == {"walk", "bus"}
    assert route_by_mode(routes, "bus").mode == "bus"


def test_no_flight_or_walk_for_medium_trip():
    m = modes(get_routes(WESTMINSTER, OXFORD))
    assert "flight" not in m and "walk" not in m
    assert {"train", "bus"} <= m


def test_long_trip_offers_flight_train_and_coach():
    assert modes(get_routes(WESTMINSTER, EDINBURGH)) == {"bus", "train", "flight"}


@pytest.mark.parametrize("end", [NEAR, SHOREDITCH, MANCHESTER, EDINBURGH])
def test_save_time_picks_fastest(end):
    routes = get_routes(WESTMINSTER, end)
    assert choose_best_route(routes, "save time").duration_min == min(r.duration_min for r in routes)


@pytest.mark.parametrize("end", [BANK, MANCHESTER, EDINBURGH])
def test_save_money_picks_cheapest(end):
    routes = get_routes(WESTMINSTER, end)
    assert choose_best_route(routes, "save money").cost_gbp == min(r.cost_gbp for r in routes)


def test_preferences_differ_on_long_trip():
    routes = get_routes(WESTMINSTER, EDINBURGH)
    assert choose_best_route(routes, "save time").mode == "flight"
    assert choose_best_route(routes, "save money").mode == "bus"


def test_balanced_excludes_very_slow_options():
    routes = get_routes(WESTMINSTER, EDINBURGH)
    best = choose_best_route(routes, "balanced")
    assert best.duration_min <= 2 * min(r.duration_min for r in routes)


def test_unknown_preference_defaults_to_fastest():
    routes = get_routes(WESTMINSTER, MANCHESTER)
    assert choose_best_route(routes, "whatever") == choose_best_route(routes, "save time")


def test_schema_and_validation():
    for r in get_routes(WESTMINSTER, MANCHESTER):
        assert r.duration_min > 0 and r.cost_gbp >= 0 and r.distance_km > 0
    with pytest.raises(ValueError):
        get_routes(None, MANCHESTER)
    with pytest.raises(ValueError):
        choose_best_route([], "save time")
    with pytest.raises(ValueError):
        estimate_cost(10, "teleport")


def test_route_by_mode_missing():
    assert route_by_mode(get_routes(WESTMINSTER, BANK), "flight") is None
