import pytest

from backend.tests.conftest import redis_unavailable


def test_skips_locally_by_default(monkeypatch):
    monkeypatch.delenv("REQUIRE_REDIS",raising=False)
    with pytest.raises(pytest.skip.Exception):
        redis_unavailable("连不上Redis")

def test_fails_when_redis_is_required(monkeypatch):
    monkeypatch.setenv("REQUIRE_REDIS","1")
    with pytest.raises(pytest.fail.Exception,match="连不上Redis"):
        redis_unavailable("连不上Redis")