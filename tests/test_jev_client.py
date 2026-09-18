"""The transport layer: caching, offline routing, fan-out, errors."""
import json

import pytest

from chorus.http import HttpError
from chorus.jev import JevClient, JevError
from chorus.jev import client as client_mod
from chorus.jev import questions as Q


def test_offline_when_no_key_is_set(cfg, monkeypatch):
    monkeypatch.delenv(cfg.jev.api_key_env, raising=False)
    cfg.jev.offline = False
    client = JevClient(cfg)
    assert client.offline
    assert cfg.jev.api_key_env in client.why_offline()


def test_offline_answers_have_the_same_shape_as_the_api(jev):
    response = jev.ask({"document": {"text": "they will hike"}},
                       {"q": Q.noul("Does it say hike?", yes={"examples": ["hike"]})})
    assert 0.0 <= response.noul("q") <= 1.0
    assert response.stub


def test_a_repeated_request_is_served_from_disk(cfg, monkeypatch):
    calls = []
    payload = {"model": "jev-latest", "answers": {"q": {"type": "noul", "noul": 0.9}},
               "usage": {"input_tokens": 100}}

    def fake_request(method, url, **kw):
        calls.append(url)
        return payload

    monkeypatch.setenv(cfg.jev.api_key_env, "k")
    monkeypatch.setattr(client_mod, "request", fake_request)
    cfg.jev.offline = False
    client = JevClient(cfg)

    first = client.ask("state", {"q": Q.noul("x")})
    second = client.ask("state", {"q": Q.noul("x")})
    assert len(calls) == 1
    assert first.noul("q") == second.noul("q") == 0.9
    assert second.cached and client.usage["cached"] == 1


def test_a_different_question_is_not_served_from_cache(cfg, monkeypatch):
    calls = []
    monkeypatch.setenv(cfg.jev.api_key_env, "k")
    monkeypatch.setattr(client_mod, "request", lambda *a, **k: (
        calls.append(1), {"model": "m", "answers": {}, "usage": {}})[1])
    cfg.jev.offline = False
    client = JevClient(cfg)
    client.ask("state", {"q": Q.noul("x")})
    client.ask("state", {"q": Q.noul("y")})
    assert len(calls) == 2


def test_the_question_version_is_part_of_the_cache_key(cfg, monkeypatch):
    """Rewording a question must invalidate the answers it could change."""
    monkeypatch.setenv(cfg.jev.api_key_env, "k")
    cfg.jev.offline = False
    client = JevClient(cfg)
    before = client._key("state", {"q": Q.noul("x")})
    monkeypatch.setattr(client_mod, "QUESTION_VERSION", "9999-99-99.9")
    after = client._key("state", {"q": Q.noul("x")})
    assert before != after


def test_a_bad_key_fails_loudly_rather_than_being_retried(cfg, monkeypatch):
    monkeypatch.setenv(cfg.jev.api_key_env, "k")

    def boom(*a, **kw):
        raise HttpError(401, '{"error":"invalid key"}')

    monkeypatch.setattr(client_mod, "request", boom)
    cfg.jev.offline = False
    with pytest.raises(JevError) as e:
        JevClient(cfg).ask("s", {"q": Q.noul("x")})
    assert cfg.jev.api_key_env in str(e.value)


def test_a_malformed_question_names_the_offending_field(cfg, monkeypatch):
    monkeypatch.setenv(cfg.jev.api_key_env, "k")

    def boom(*a, **kw):
        raise HttpError(422, '{"detail":"questions.stance.criteria too short"}')

    monkeypatch.setattr(client_mod, "request", boom)
    cfg.jev.offline = False
    with pytest.raises(JevError) as e:
        JevClient(cfg).ask("s", {"q": Q.noul("x")})
    assert "criteria" in str(e.value)


def test_one_failure_in_a_fan_out_does_not_lose_the_others(cfg, monkeypatch):
    monkeypatch.setenv(cfg.jev.api_key_env, "k")
    cfg.jev.offline = False
    cfg.jev.max_concurrency = 2
    client = JevClient(cfg)

    def flaky(method, url, *, json_body=None, **kw):
        if "boom" in json.dumps(json_body):
            raise HttpError(500, "server on fire")
        return {"model": "m", "answers": {"q": {"type": "noul", "noul": 0.5}},
                "usage": {}}

    monkeypatch.setattr(client_mod, "request", flaky)
    seen = []
    out = client.ask_many(
        [("ok1", "fine", {"q": Q.noul("x")}),
         ("bad", "boom", {"q": Q.noul("x")}),
         ("ok2", "also fine", {"q": Q.noul("x")})],
        on_error=lambda key, e: seen.append(key),
    )
    assert set(out) == {"ok1", "ok2"}
    assert seen == ["bad"]


def test_usage_and_cost_are_tracked(cfg, monkeypatch):
    monkeypatch.setenv(cfg.jev.api_key_env, "k")
    monkeypatch.setattr(client_mod, "request", lambda *a, **k: {
        "model": "m", "answers": {"q": {"type": "noul", "noul": 0.1}},
        "usage": {"input_tokens": 1_000_000}})
    cfg.jev.offline = False
    client = JevClient(cfg)
    client.ask("s", {"q": Q.noul("x")})
    assert client.usage["input_tokens"] == 1_000_000
    assert abs(client.cost_estimate() - cfg.jev.price_in_usd_per_mtok) < 1e-9


def test_offline_mode_never_builds_a_request(cfg, monkeypatch):
    monkeypatch.setattr(client_mod, "request", lambda *a, **k: pytest.fail("called"))
    cfg.jev.offline = True
    JevClient(cfg).ask("s", {"q": Q.noul("x")})


def test_a_truncated_cache_file_is_ignored_not_fatal(cfg, monkeypatch):
    monkeypatch.setenv(cfg.jev.api_key_env, "k")
    monkeypatch.setattr(client_mod, "request", lambda *a, **k: {
        "model": "m", "answers": {"q": {"type": "noul", "noul": 0.4}}, "usage": {}})
    cfg.jev.offline = False
    client = JevClient(cfg)
    key = client._key("s", {"q": Q.noul("x")})
    (client.cache_dir / f"{key}.json").write_text("{not json")
    assert client.ask("s", {"q": Q.noul("x")}).noul("q") == 0.4


def test_score_answers_are_scaled_by_their_own_rubric(cfg):
    from chorus.jev.client import Response
    r = Response({"answers": {"s": {"type": "score", "score": 2.0,
                                    "legend": {"0": "a", "1": "b", "2": "c"},
                                    "probabilities": {}, "confidence": 0.5}}})
    assert r.unit_score("s") == 1.0
    assert r.score("s")[1] == 3
