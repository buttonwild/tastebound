"""YouCam client: request shapes, unit caps, balance floor, refunds. Synthetic payloads only."""

import pytest

from tastebound.limits import LimitSpec, QuotaExceeded, UsageLimiter
from tastebound.style import (
    CLOTH_UNITS, HARD_MAX_UNITS, HARD_MIN_BALANCE, Garment, UnitGuard, YouCamClient, YouCamError, skin_units,
)


class Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class FakeYouCam:
    """Routes requests by (method, path) to scripted responses."""

    def __init__(self, balance=1000.0, task_statuses=("running", "success"), result=None, create_status=200):
        self.headers, self.calls = {}, []
        self.balance, self.statuses = balance, list(task_statuses)
        self.result = result or {"url": "https://example.com/out.jpg"}
        self.create_status = create_status

    def request(self, method, url, **kw):
        path = url.split("makeupar.com", 1)[1]
        self.calls.append((method, path, kw))
        if path == "/s2s/v1.0/client/credit":
            return Resp({"status": 200, "results": [{"amount_dec": self.balance}]})
        if path == "/s2s/v2.0/task/template/cloth":
            return Resp({"status": 200, "data": {"templates": [{"id": "t1", "title": "Linen Set", "thumb": "https://x/t.jpg", "category_name": "Feminine"}]}})
        if method == "POST" and path.startswith("/s2s/v2.0/task/"):
            return Resp({"status": 200, "data": {"task_id": "task-1"}}, self.create_status)
        if method == "GET" and "/task/" in path:
            status = self.statuses.pop(0) if self.statuses else "success"
            data = {"task_status": status}
            if status == "success":
                data["results"] = self.result
            if status == "error":
                data["error"] = "error_src_face_too_small"
            return Resp({"status": 200, "data": data})
        return Resp({"error": "not found"}, 404)

    def posted(self, path):
        return [kw["json"] for m, p, kw in self.calls if m == "POST" and p == path]


def client(tmp_path, fake, **kw):
    return YouCamClient(
        "k", session=fake, sleep=lambda s: None,
        limiter=kw.pop("limiter", UsageLimiter("youcam", LimitSpec(100, 100), tmp_path / "lim.json")),
        guard=kw.pop("guard", UnitGuard(tmp_path / "units.json")), **kw,
    )


def test_try_on_with_reference_garment_uses_documented_fields(tmp_path):
    fake = FakeYouCam()
    c = client(tmp_path, fake)
    res = c.try_on(Garment("dress", image_url="https://e/g.png", category="full_body"), src_file_id="f1")
    assert res.image_url == "https://example.com/out.jpg" and res.units == CLOTH_UNITS
    assert fake.posted("/s2s/v2.0/task/cloth") == [{"src_file_id": "f1", "ref_file_url": "https://e/g.png", "garment_category": "full_body"}]
    assert fake.headers["Authorization"] == "Bearer k"
    assert c.guard.snapshot()["units_used"] == CLOTH_UNITS


def test_try_on_with_template(tmp_path):
    fake = FakeYouCam()
    client(tmp_path, fake).try_on(Garment("set", template_id="t1"), src_file_url="https://e/p.jpg")
    assert fake.posted("/s2s/v2.0/task/cloth") == [{"src_file_url": "https://e/p.jpg", "template_id": "t1"}]


def test_failed_task_releases_units(tmp_path):
    fake = FakeYouCam(task_statuses=["running", "error"])
    c = client(tmp_path, fake)
    with pytest.raises(YouCamError, match="face_too_small"):
        c.analyze_skin(["moisture"], src_file_url="https://e/f.png")
    assert c.guard.snapshot()["units_used"] == 0


def test_create_failure_releases_units(tmp_path):
    c = client(tmp_path, FakeYouCam(create_status=400))
    with pytest.raises(YouCamError):
        c.try_on(Garment("set", template_id="t1"), src_file_url="https://e/p.jpg")
    assert c.guard.snapshot()["units_used"] == 0


def test_timeout_keeps_reservation(tmp_path):
    c = client(tmp_path, FakeYouCam(task_statuses=["running"] * 100))
    with pytest.raises(YouCamError, match="still processing"):
        c.try_on(Garment("set", template_id="t1"), src_file_url="https://e/p.jpg", timeout_s=10)
    assert c.guard.snapshot()["units_used"] == CLOTH_UNITS  # may still be billed


def test_unit_cap_blocks_before_creating_task(tmp_path):
    fake = FakeYouCam()
    c = client(tmp_path, fake, guard=UnitGuard(tmp_path / "u.json", max_units=3))
    c.try_on(Garment("a", template_id="t1"), src_file_url="https://e/p.jpg")
    with pytest.raises(QuotaExceeded, match="unit cap"):
        c.try_on(Garment("a", template_id="t1"), src_file_url="https://e/p.jpg")
    assert len(fake.posted("/s2s/v2.0/task/cloth")) == 1


def test_real_balance_floor_blocks_even_with_fresh_ledger(tmp_path):
    """A wiped ledger (free host restart) must not unlock spending: the floor uses the real balance."""
    fake = FakeYouCam(balance=HARD_MIN_BALANCE + 1)
    c = client(tmp_path, fake)
    with pytest.raises(QuotaExceeded, match="floor"):
        c.try_on(Garment("a", template_id="t1"), src_file_url="https://e/p.jpg")
    assert fake.posted("/s2s/v2.0/task/cloth") == []
    assert c.guard.snapshot()["units_used"] == 0


def test_caps_cannot_be_loosened_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TASTEBOUND_YOUCAM_MAX_UNITS", "100000")
    monkeypatch.setenv("TASTEBOUND_YOUCAM_MIN_BALANCE", "0")
    c = YouCamClient("k", session=FakeYouCam(), guard=UnitGuard(tmp_path / "u.json"))
    assert c.guard.max_units == HARD_MAX_UNITS and c.min_balance == HARD_MIN_BALANCE


def test_hourly_task_limit(tmp_path):
    fake = FakeYouCam()
    c = client(tmp_path, fake, limiter=UsageLimiter("youcam", LimitSpec(1, 10), tmp_path / "l.json"))
    c.try_on(Garment("a", template_id="t1"), src_file_url="https://e/p.jpg")
    with pytest.raises(QuotaExceeded, match="hourly"):
        c.try_on(Garment("a", template_id="t1"), src_file_url="https://e/p.jpg")


def test_skin_report_parsing_and_units():
    assert (skin_units(1), skin_units(4), skin_units(5), skin_units(16)) == (9, 9, 12, 16)
    with pytest.raises(ValueError):
        skin_units(17)


def test_skin_request_shape_and_scores(tmp_path):
    fake = FakeYouCam(result={"output": [{"type": "moisture", "ui_score": 61, "raw_score": 0.4}, {"type": "skin_age", "score": 29}]})
    rep = client(tmp_path, fake).analyze_skin(["moisture", "oiliness"], src_file_id="f1")
    assert fake.posted("/s2s/v2.0/task/skin-analysis") == [{"dst_actions": ["moisture", "oiliness"], "format": "json", "src_file_id": "f1"}]
    assert rep.scores == {"moisture": 61.0} and rep.skin_age == 29 and rep.units == 9


def test_input_validation(tmp_path):
    c = client(tmp_path, FakeYouCam())
    with pytest.raises(ValueError):
        c.try_on(Garment("a", template_id="t1"))  # no source
    with pytest.raises(ValueError):
        c.try_on(Garment("a", image_url="https://e/g.png", category="hats"), src_file_url="https://e/p.jpg")
    with pytest.raises(ValueError):
        c.analyze_skin(["hd_pore"], src_file_url="https://e/f.png")  # HD not mixed into SD
    with pytest.raises(YouCamError):
        c.upload(b"x" * (10 * 1024 * 1024 + 1))


def test_list_garments_is_free_and_parsed(tmp_path):
    fake = FakeYouCam()
    c = client(tmp_path, fake)
    g = c.list_garments()
    assert g[0].template_id == "t1" and g[0].label == "Linen Set"
    assert c.guard.snapshot()["units_used"] == 0


def test_floor_does_not_depend_on_local_state(tmp_path, monkeypatch):
    """The floor reads the real balance on every task; ledgers and state dirs play no part."""
    fake = FakeYouCam(balance=1000)
    c = client(tmp_path, fake)
    c.try_on(Garment("a", template_id="t1"), src_file_url="https://e/p.jpg")
    fake.balance = HARD_MIN_BALANCE + 1  # real balance drops (e.g. spent elsewhere)
    with pytest.raises(QuotaExceeded, match="floor"):
        c.try_on(Garment("a", template_id="t1"), src_file_url="https://e/p.jpg")
    balance_reads = [p for m, p, kw in fake.calls if p == "/s2s/v1.0/client/credit"]
    assert len(balance_reads) == 2  # read live each time, never cached


def test_balance_read_failure_fails_closed(tmp_path):
    class NoBalance(FakeYouCam):
        def request(self, method, url, **kw):
            if url.endswith("/client/credit"):
                return Resp({"error": "down"}, 503)
            return super().request(method, url, **kw)

    fake = NoBalance()
    c = client(tmp_path, fake)
    with pytest.raises(YouCamError):
        c.try_on(Garment("a", template_id="t1"), src_file_url="https://e/p.jpg")
    assert fake.posted("/s2s/v2.0/task/cloth") == [] and c.guard.snapshot()["units_used"] == 0


def test_floor_status_reports_headroom(tmp_path):
    st = client(tmp_path, FakeYouCam(balance=900)).floor_status(units=2)
    assert st == {"balance": 900.0, "floor": HARD_MIN_BALANCE, "headroom": 900.0 - HARD_MIN_BALANCE, "allowed": True}
