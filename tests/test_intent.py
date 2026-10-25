from tastebound.intent import extract_intent, keyword_intent


def test_balanced_beats_cheap():
    assert keyword_intent("cheapest but not too slow")["preference"] == "balanced"


def test_preferences():
    assert keyword_intent("as quick as possible")["preference"] == "save time"
    assert keyword_intent("on a budget")["preference"] == "save money"


def test_modes_use_word_starts():
    assert keyword_intent("any bus option?")["mode_request"] == "bus"
    assert keyword_intent("by railway")["mode_request"] == "train"
    assert "mode_request" not in keyword_intent("a nice hiking trail")


def test_acknowledgement_and_restart():
    assert keyword_intent("thanks!")["acknowledgement"] is True
    assert "acknowledgement" not in keyword_intent("thanks, now show me trains")
    assert keyword_intent("let's start over")["restart_trip"] is True


def test_start_time_and_tastes():
    assert keyword_intent("tomorrow at 9am")["start_time"] == "tomorrow at 9am"
    assert keyword_intent("I love Radiohead, ramen and Wes Anderson films")["tastes"] == [
        "Radiohead", "ramen", "Wes Anderson films"]


class _LLM:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def complete_json(self, *a, **k):
        if self.error:
            raise self.error
        return self.result


def test_llm_values_are_sanitized_and_take_precedence():
    llm = _LLM({"preference": "save money", "mode_request": "flight", "tastes": ["jazz", 3, ""], "restart_trip": "yes"})
    intent = extract_intent("quick please, I like jazz", llm)
    assert intent["preference"] == "save money"
    assert "mode_request" not in intent  # modes come from keywords only
    assert intent["tastes"] == ["jazz"]
    assert "restart_trip" not in intent


def test_hallucinated_free_text_values_are_dropped():
    # Real failure seen live: a bare place name came back with invented fields
    llm = _LLM({"start_time": "tomorrow 9am", "tastes": ["Radiohead"], "preference": "balanced"})
    intent = extract_intent("York", llm)
    assert "start_time" not in intent and "tastes" not in intent


def test_llm_failure_falls_back_to_keywords():
    assert extract_intent("cheap", _LLM(error=RuntimeError("down")))["preference"] == "save money"
