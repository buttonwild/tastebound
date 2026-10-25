from types import SimpleNamespace

from conftest import FakeLLM, FakeQloo, FakeTavily, tool_call
from tastebound.agent import Services, TripAgent


def run(agent, *messages):
    return [agent.handle(m) for m in messages]


def test_full_flow_offline_without_any_keys(geocoder):
    agent = TripAgent(Services(geocoder=geocoder))
    replies = run(agent, "Westminster", "Edinburgh", "tomorrow 9am", "cheapest", "skip")
    assert "Westminster" in replies[0].text
    assert "Edinburgh" in replies[1].text
    assert "save time" in replies[2].text
    assert "Tell me a few things you love" in replies[3].text
    assert "Recommended journey" in replies[4].text and "Bus" in replies[4].text
    assert agent.completed

    assert "Available options" in agent.handle("show all options").text
    assert "Train option" in agent.handle("train option").text
    assert "Flight" in agent.handle("fastest").text
    assert "welcome" in agent.handle("thanks").text
    assert "starting from" in agent.handle("start over").text
    assert not agent.completed


def test_unknown_place_is_retried(geocoder):
    agent = TripAgent(Services(geocoder=geocoder))
    assert "couldn't find" in agent.handle("Atlantis").text
    assert agent.ctx.start is None


def test_time_and_preference_in_one_message(geocoder):
    agent = TripAgent(Services(geocoder=geocoder))
    replies = run(agent, "Westminster", "Manchester", "tomorrow morning, as fast as possible")
    assert agent.ctx.preference == "save time"
    assert "Tell me a few things you love" in replies[-1].text


def test_tastes_mentioned_early_skip_the_question_and_feed_qloo(geocoder):
    qloo = FakeQloo()
    agent = TripAgent(Services(geocoder=geocoder, qloo=qloo, tavily=FakeTavily()))
    replies = run(agent, "Westminster", "Manchester", "today at 5pm", "I love Joy Division and balanced please")
    final = replies[-1]
    assert "Recommended journey" in final.text
    assert "Test Cafe One" in final.text and "Joy Division" in final.text
    assert "Test headline" in final.text
    assert any(c[0] == "places" and c[1] == 53.48 for c in qloo.calls)  # searched near destination
    assert any(t.startswith("find_taste_places") for t in final.trace)


def test_free_text_tastes_answer_is_used(geocoder):
    qloo = FakeQloo()
    agent = TripAgent(Services(geocoder=geocoder, qloo=qloo))
    run(agent, "Westminster", "Manchester", "now", "balanced", "Radiohead, ramen")
    assert agent.ctx.tastes == ["Radiohead", "ramen"]
    assert ("resolve", ("Radiohead", "ramen")) in qloo.calls


def test_concierge_handles_open_ended_followup(geocoder):
    llm = FakeLLM(turns=[
        # arrival brief during planning
        SimpleNamespace(content="Brief.", tool_calls=None),
        # follow-up: one tool round then the answer
        SimpleNamespace(content=None, tool_calls=[tool_call("c1", "find_taste_places", {"where": "destination", "vibe": "brunch"})]),
        SimpleNamespace(content="<think>hmm</think>Try **Test Cafe One**.", tool_calls=None),
    ])
    qloo = FakeQloo()
    agent = TripAgent(Services(geocoder=geocoder, llm=llm, qloo=qloo))
    run(agent, "Westminster", "Manchester", "now", "balanced", "skip")
    reply = agent.handle("Where should I get brunch when I arrive in Manchester?")
    assert reply.text == "Try **Test Cafe One**."
    assert ("tags", "brunch") in qloo.calls
    assert reply.trace and reply.trace[0].startswith("find_taste_places")


def test_long_message_is_not_misread_as_route_command(geocoder):
    llm = FakeLLM(turns=[
        SimpleNamespace(content="Brief.", tool_calls=None),
        SimpleNamespace(content="Here are cheap brunch spots.", tool_calls=None),
    ])
    agent = TripAgent(Services(geocoder=geocoder, llm=llm, qloo=FakeQloo()))
    run(agent, "Westminster", "Edinburgh", "now", "save time", "skip")
    chosen = agent.ctx.chosen
    assert agent.handle("any cheap brunch places near the station when I arrive?").text == "Here are cheap brunch spots."
    assert agent.ctx.chosen == chosen


def test_concierge_failure_falls_back(geocoder):
    class Broken(FakeLLM):
        def chat_with_tools(self, *a, **k):
            raise RuntimeError("down")

    agent = TripAgent(Services(geocoder=geocoder, llm=Broken(), qloo=FakeQloo()))
    final = run(agent, "Westminster", "Manchester", "now", "balanced", "I love jazz")[-1]
    assert "Recommended journey" in final.text and "Test Cafe One" in final.text
    assert "You can say" in agent.handle("what about museums near there?").text


def test_mode_request_wins_over_llm_show_all(geocoder):
    llm = FakeLLM(intents={"any bus option?": {"show_all_options": True}},
                  turns=[SimpleNamespace(content="Brief.", tool_calls=None)])
    agent = TripAgent(Services(geocoder=geocoder, llm=llm, qloo=FakeQloo()))
    run(agent, "Westminster", "Edinburgh", "now", "save time", "skip")
    assert "Bus option" in agent.handle("any bus option?").text


def test_preference_given_at_tastes_question_is_not_a_taste(geocoder):
    agent = TripAgent(Services(geocoder=geocoder, qloo=FakeQloo()))
    run(agent, "Westminster", "Edinburgh", "now", "save time")
    reply = agent.handle("actually save money")
    assert agent.ctx.tastes == [] and agent.ctx.preference == "save money"
    assert "Recommended journey" in reply.text and "Bus" in reply.text
