"""
健康检查接口。部署脚本和容器编排靠它判断服务能不能用：

- GET /health        存活检查（liveness）：进程活着、能响应请求就返回 200，不碰任何外部依赖
- GET /health/ready  就绪检查（readiness）：Redis 与 MySQL 都连得上才返回 200，否则 503

分成两个接口，是因为两者回答的问题不同：数据库短暂抖动时进程本身没坏，
不该被当成「死了」而反复重启；但部署时必须确认依赖都通，才能判定新版本上线成功。
"""
import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from backend.model import engine
from backend.utils.redis_client import get_redis_client

health_router = APIRouter(prefix="/health", tags=["health"])

# 单项检查的限时：依赖卡住时，健康检查本身不能跟着卡住
CHECK_TIMEOUT = 2.0


async def check_redis() -> None:
    await get_redis_client().client.ping()


async def check_database() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


CHECKS = {"redis": check_redis, "database": check_database}


async def _run_check(check) -> str:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT)
        return "ok"
    except Exception as e:
        # 只返回异常类型，不返回异常内容：内容里可能带连接串、主机名等内部信息
        return f"error: {type(e).__name__}"


@health_router.get("")
async def liveness() -> dict:
    return {"status": "ok"}


@health_router.get("/ready")
async def readiness() -> JSONResponse:
    names = list(CHECKS)
    # gather 并发执行，总耗时约等于最慢的一项，而不是全部相加
    outcomes = await asyncio.gather(*(_run_check(CHECKS[name]) for name in names))
    checks = dict(zip(names, outcomes))
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ok" if ready else "unavailable", "checks": checks},
    )
