import importlib

import analysis_cache


def reload_cache(monkeypatch, tmp_path, version="test.atomic-v1"):
    monkeypatch.setenv("ANALYZE_CACHE_DB", str(tmp_path / "analysis_cache.db"))
    monkeypatch.setenv("ANALYZE_CACHE_PERSISTENT", "1")
    monkeypatch.setenv("ANALYZE_CACHE_MAX_ENTRIES", "8")
    monkeypatch.setenv("ANALYZE_CACHE_TTL_DAYS", "30")
    monkeypatch.setenv("SCORER_VERSION", version)
    return importlib.reload(analysis_cache)


def test_persistent_cache_round_trip_sanitizes_request_context(monkeypatch, tmp_path):
    cache = reload_cache(monkeypatch, tmp_path)
    key = cache.analyze_cache_key("Resume text", "Job description")
    response = {
        "match_score": 72,
        "debug": {"temporary": True},
        "user": {"email": "candidate@example.com"},
    }

    cache.set_cached_response(key, response, "Resume text", "Job description")
    cache = importlib.reload(cache)

    cached = cache.get_cached_response(key)
    assert cached["match_score"] == 72
    assert "debug" not in cached
    assert "user" not in cached


def test_cache_key_changes_with_scorer_version(monkeypatch, tmp_path):
    cache = reload_cache(monkeypatch, tmp_path, version="version-a")
    key_a = cache.analyze_cache_key("Resume text", "Job description")

    cache = reload_cache(monkeypatch, tmp_path, version="version-b")
    key_b = cache.analyze_cache_key("Resume text", "Job description")

    assert key_a != key_b


def test_secondary_cache_round_trip_and_versioning(monkeypatch, tmp_path):
    cache = reload_cache(monkeypatch, tmp_path, version="secondary-a")
    payload = {"resume": "abc", "job": "def", "role_fit": "ghi"}
    key_a = cache.secondary_cache_key("recruiter-view", payload)

    cache.set_cached_secondary_response(
        key_a,
        "recruiter-view",
        {"recruiter_view": {"verdict": {"decision": "maybe"}}, "debug": {"drop": True}},
    )
    cache = importlib.reload(cache)

    cached = cache.get_cached_secondary_response(key_a, "recruiter-view")
    assert cached["recruiter_view"]["verdict"]["decision"] == "maybe"
    assert "debug" not in cached

    cache = reload_cache(monkeypatch, tmp_path, version="secondary-b")
    key_b = cache.secondary_cache_key("recruiter-view", payload)

    assert key_a != key_b
    assert cache.get_cached_secondary_response(key_a, "recruiter-view") is None
