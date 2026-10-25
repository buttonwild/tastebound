"""Operator panel UI, driven headlessly with fakes (no network)."""

import json

from streamlit.testing.v1 import AppTest

STRONG = "x" * 10 + "correct-horse-battery"


def panel_app(passphrase, ledger, live=False):
    import sys
    sys.path.insert(0, __import__("os").getcwd())
    from tastebound.errands import ErrandGuard, ErrandsClient, OperatorGate
    from tastebound.limits import LimitSpec, UsageLimiter
    from tastebound.operator_panel import render_operator_panel

    class FakeTransport:
        def call(self, tool, args):
            assert tool in ("get_account", "get_contract"), f"unexpected write call {tool}"
            return {"balance": {"amount": 45.0}} if tool == "get_account" else {"price": None, "state": "estimating"}

    class FakeYouCam:
        def floor_status(self, units=0):
            return {"balance": 900.0, "floor": 440.0, "headroom": 460.0, "allowed": True}

    gate = OperatorGate(passphrase, ledger + "/op.json", sleep=lambda s: None)
    errands = ErrandsClient(FakeTransport(), guard=ErrandGuard(ledger + "/g.json"),
                            limiter=UsageLimiter("tendem", LimitSpec(100, 100), ledger + "/l.json"), live=live)
    render_operator_panel(gate, FakeYouCam(), errands)


def run(tmp_path, passphrase=STRONG, **kw):
    return AppTest.from_function(panel_app, args=(passphrase, str(tmp_path)), kwargs=kw, default_timeout=30).run()


def captions(at):
    return " ".join(c.value for c in at.sidebar.caption)


def test_disabled_without_passphrase(tmp_path):
    at = run(tmp_path, passphrase="")
    assert "disabled" in captions(at) and not at.sidebar.text_input


def test_wrong_then_right_passphrase(tmp_path):
    at = run(tmp_path)
    at.sidebar.text_input(key="operator_attempt").set_value("wrong").run()
    at.sidebar.button[0].click().run()
    assert any("Wrong passphrase" in e.value for e in at.sidebar.error)
    failures = json.loads((tmp_path / "op.json").read_text())["failures"]
    assert len(failures) == 1

    at.sidebar.text_input(key="operator_attempt").set_value(STRONG).run()
    at.sidebar.button[0].click().run()
    text = captions(at)
    assert "YouCam: balance 900 units · floor 440" in text and "✅ active" in text
    assert "Tendem: balance $45.00 · floor $20.00" in text and "disarmed" in text
    assert not at.exception


def test_create_disabled_when_disarmed(tmp_path):
    at = run(tmp_path)
    at.sidebar.text_input(key="operator_attempt").set_value(STRONG).run()
    at.sidebar.button[0].click().run()
    for label, value in [("Restaurant", "Test Bistro"), ("Address", "1 Test St"), ("City", "Manchester"), ("Date of call (e.g. Saturday 25 October 2026)", "Saturday")]:
        next(t for t in at.sidebar.text_input if t.label == label).set_value(value)
    next(b for b in at.sidebar.button if b.label == "Preview brief").click().run()
    assert any("Do NOT make a booking" in c.value for c in at.sidebar.code)
    create = next(b for b in at.sidebar.button if b.label == "Create Tendem task")
    assert create.disabled
    assert "disarmed" in captions(at)
