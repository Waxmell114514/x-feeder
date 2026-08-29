"""The model call path, exercised against a fake SDK client.

There is no API key in CI, so these pin the parts that would otherwise only
be discovered in production: that the frozen rubric is sent as a cached
system block, that the disk cache actually prevents a second call, and that
a refusal degrades instead of crashing.
"""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from xfeeder.llm.client import LLMClient, RefusalError


class Out(BaseModel):
    verdict: str


class FakeMessages:
    def __init__(self, result, stop_reason="end_turn"):
        self.result, self.stop_reason = result, stop_reason
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed_output=self.result,
            stop_reason=self.stop_reason,
            stop_details=SimpleNamespace(category="cyber"),
            content=[],
            usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                  cache_read_input_tokens=900),
        )


class FakeClient:
    def __init__(self, result, stop_reason="end_turn"):
        self.messages = FakeMessages(result, stop_reason)
        self.beta = SimpleNamespace(messages=self.messages)


@pytest.fixture
def llm(cfg, tmp_path):
    cfg.llm.offline = False
    cfg.llm.cache_dir = str(tmp_path / "cache")
    return LLMClient(cfg)


def call(llm, user="posts here"):
    return llm.structured(system="RUBRIC", user=user, schema=Out,
                          model="claude-opus-5", effort="low")


def test_returns_the_parsed_model(llm):
    llm._client = FakeClient(Out(verdict="我们相信会加息"))
    assert call(llm).verdict == "我们相信会加息"


def test_frozen_rubric_is_sent_as_a_cached_system_block(llm):
    fake = FakeClient(Out(verdict="v"))
    llm._client = fake
    call(llm)
    system = fake.messages.calls[0]["system"]
    assert system[0]["text"] == "RUBRIC"
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # the volatile part must come after the breakpoint, not inside it
    assert "posts here" not in system[0]["text"]


def test_effort_is_passed_through(llm):
    fake = FakeClient(Out(verdict="v"))
    llm._client = fake
    call(llm)
    assert fake.messages.calls[0]["output_config"]["effort"] == "low"


def test_identical_requests_are_served_from_disk(llm):
    fake = FakeClient(Out(verdict="v"))
    llm._client = fake
    call(llm)
    call(llm)
    assert len(fake.messages.calls) == 1
    assert llm.usage["cached"] == 1


def test_a_different_request_is_not_served_from_cache(llm):
    fake = FakeClient(Out(verdict="v"))
    llm._client = fake
    call(llm, "posts A")
    call(llm, "posts B")
    assert len(fake.messages.calls) == 2


def test_refusal_raises_rather_than_returning_nonsense(llm):
    llm._client = FakeClient(Out(verdict="v"), stop_reason="refusal")
    with pytest.raises(RefusalError):
        call(llm)


def test_usage_and_cost_are_tracked(llm):
    llm._client = FakeClient(Out(verdict="v"))
    call(llm)
    assert llm.usage["input_tokens"] == 100
    assert llm.usage["cache_read_tokens"] == 900
    assert llm.cost_estimate() == pytest.approx(
        (100 * 5.0 + 900 * 0.5 + 20 * 25.0) / 1e6)


def test_offline_mode_never_builds_a_client(cfg, tmp_path):
    from xfeeder.llm.client import LLMUnavailable
    cfg.llm.offline = True
    cfg.llm.cache_dir = str(tmp_path / "c")
    with pytest.raises(LLMUnavailable):
        _ = LLMClient(cfg).client
