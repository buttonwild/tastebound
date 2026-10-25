import json
from types import SimpleNamespace

from conftest import PLACES, FakeLLM, FakeQloo, FakeTavily, tool_call
from tastebound.concierge import MAX_TOOL_ROUNDS, Toolbox, TripContext, run_concierge
from tastebound.transport import get_routes


def ctx():
    c = TripContext(start=PLACES["westminster"], destination=PLACES["manchester"], tastes=["Oasis"])
    c.routes = get_routes(c.start.coords, c.destination.coords)
    return c


def test_tool_results_are_fed_back_to_the_model():
    llm = FakeLLM(turns=[
        SimpleNamespace(content=None, tool_calls=[
            tool_call("a", "get_route_options", {}),
            tool_call("b", "search_live_travel_info", {"query": "rail strike Manchester"}),
        ]),
        SimpleNamespace(content="All clear.", tool_calls=None),
    ])
    tavily = FakeTavily()
    box = Toolbox(ctx(), FakeQloo(), tavily)
    assert run_concierge(llm, box, "Anything I should know?").startswith("All clear.\n\n**Sources**")
    final_messages = llm.seen_messages[-1]
    tool_msgs = [m for m in final_messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["a", "b"]
    assert json.loads(tool_msgs[0]["content"])["routes"]
    assert tavily.queries == ["rail strike Manchester"]
    assert "Oasis" in final_messages[1]["content"]  # trip context reaches the model


def test_tool_errors_are_reported_not_raised():
    box = Toolbox(ctx(), qloo=None, tavily=None)
    assert "error" in box.run("find_taste_places", {"where": "destination"})
    assert "error" in box.run("search_live_travel_info", {"query": "x"})
    assert "error" in box.run("nope", {})
    assert "error" in box.run("get_route_options", {"bogus": 1})


def test_default_tastes_come_from_context():
    qloo = FakeQloo()
    result = Toolbox(ctx(), qloo, None).run("find_taste_places", {"where": "start"})
    assert result["matched_tastes"] == ["Oasis"]
    assert qloo.calls[-1][1] == 51.5  # searched near the start


def test_loop_is_bounded():
    endless = [SimpleNamespace(content=None, tool_calls=[tool_call(str(i), "get_route_options", {})]) for i in range(MAX_TOOL_ROUNDS)]
    llm = FakeLLM(turns=endless + [SimpleNamespace(content="Done.", tool_calls=None)])
    assert run_concierge(llm, Toolbox(ctx(), None, None), "loop") == "Done."
    assert len(llm.seen_messages) == MAX_TOOL_ROUNDS + 1


def test_web_sources_are_appended_when_not_cited():
    llm = FakeLLM(turns=[
        SimpleNamespace(content=None, tool_calls=[tool_call("a", "search_live_travel_info", {"query": "strikes"})]),
        SimpleNamespace(content="No strikes reported.", tool_calls=None),
    ])
    out = run_concierge(llm, Toolbox(ctx(), None, FakeTavily()), "strikes?")
    assert out.startswith("No strikes reported.")
    assert "**Sources**" in out and "[Test headline](https://example.com/a)" in out


def test_sources_already_cited_are_not_repeated():
    llm = FakeLLM(turns=[
        SimpleNamespace(content=None, tool_calls=[tool_call("a", "search_live_travel_info", {"query": "x"})]),
        SimpleNamespace(content="See [this](https://example.com/a).", tool_calls=None),
    ])
    assert run_concierge(llm, Toolbox(ctx(), None, FakeTavily()), "x") == "See [this](https://example.com/a)."


def test_repeated_place_lookup_is_cached():
    qloo = FakeQloo()
    box = Toolbox(ctx(), qloo, None)
    box.run("find_taste_places", {"where": "destination", "tastes": ["Oasis"]})
    box.run("find_taste_places", {"where": "destination"})  # same effective query (context tastes)
    assert [c[0] for c in qloo.calls].count("places") == 1
