"""Offline: a fake client stands in for Token Factory, so these cost nothing."""
from types import SimpleNamespace as NS

import pytest

import nebius_client as nc


class Fake:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        return NS(choices=[NS(message=NS(content=self.replies.pop(0)))], usage=NS(prompt_tokens=100, completion_tokens=50))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(nc, "_prices", dict(nc.FALLBACK_PRICES))


def test_thinking_is_off_and_cost_is_priced():
    api, b = Fake("hello"), nc.Budget(1)
    r = nc.chat("nano", [], b, api=api)
    assert api.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert r["cost_usd"] == pytest.approx(100 * 6e-8 + 50 * 2.4e-7)
    assert b.spent == r["cost_usd"]


def test_think_block_is_stripped():
    assert nc.chat("ultra", [], nc.Budget(1), thinking=True, api=Fake("x</think>\nanswer"))["text"] == "answer"


def test_cap_refuses_before_calling():
    api, b = Fake("x"), nc.Budget(0.1)
    b.spent = 0.1
    with pytest.raises(RuntimeError):
        nc.chat("nano", [], b, api=api)
    assert not api.calls


def test_wrong_shape_is_retried_then_accepted():
    api = Fake('{"neutral": {"text": "nested"}}', '```json\n{"neutral": "plain"}\n```')
    assert nc.chat_json("super", "s", "u", nc.Budget(1), {"neutral": str}, api=api) == {"neutral": "plain"}
    assert len(api.calls) == 2


def test_bad_json_fails_after_retries():
    with pytest.raises(ValueError):
        nc.chat_json("super", "s", "u", nc.Budget(1), {"a": str}, api=Fake("no", "still no"))
