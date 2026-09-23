import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.health_api as health


async def _ok():
    return None


async def _refused():
    raise ConnectionRefusedError("redis://:secret-password@10.0.0.5:6379")


async def _hang():
    await asyncio.sleep(30)


@pytest.fixture
def client():
    """只挂健康检查路由的最小应用：不触发 main.py 的启动钩子（建表、连库）"""
    app = FastAPI()
    app.include_router(health.health_router)
    return TestClient(app)


def test_liveness_does_not_touch_dependencies(client, monkeypatch):
    """依赖全挂了，存活检查照样 200——进程本身没坏，不该被反复重启"""
    monkeypatch.setattr(health, "CHECKS", {"redis": _refused, "database": _refused})
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_all_dependencies_are_up(client, monkeypatch):
    monkeypatch.setattr(health, "CHECKS", {"redis": _ok, "database": _ok})
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"redis": "ok", "database": "ok"}}


def test_not_ready_when_a_dependency_is_down(client, monkeypatch):
    monkeypatch.setattr(health, "CHECKS", {"redis": _refused, "database": _ok})
    response = client.get("/health/ready")
    assert response.status_code == 503, "依赖不通必须是 503，否则部署脚本会以为新版本起来了"
    assert response.json()["checks"] == {"redis": "error: ConnectionRefusedError", "database": "ok"}


def test_error_details_are_not_leaked(client, monkeypatch):
    """异常信息里可能有密码、内网地址，而健康检查接口通常不鉴权，只能返回异常类型"""
    monkeypatch.setattr(health, "CHECKS", {"redis": _refused})
    body = client.get("/health/ready").text
    assert "secret-password" not in body and "10.0.0.5" not in body


def test_hanging_dependency_times_out(client, monkeypatch):
    """依赖卡住时健康检查不能跟着卡住，否则部署脚本的等待就失去了意义"""
    monkeypatch.setattr(health, "CHECK_TIMEOUT", 0.1)
    monkeypatch.setattr(health, "CHECKS", {"database": _hang})
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "error: TimeoutError"}


def test_main_app_serves_health_routes():
    from backend.main import app
    paths = {route.path for route in app.routes}
    assert {"/health", "/health/ready"} <= paths
