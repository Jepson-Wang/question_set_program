import dataclasses

import pytest

from backend.agents.rag.config import RagSettings


@pytest.fixture
def rag_settings(tmp_path):
    """
    测试用配置：目录全部指向临时目录；裁判与生成模型设成不同家族，
    保证家族校验通过；去重阈值放宽到 0.9，适配 HashingEmbedder 的精度。
    """
    return dataclasses.replace(
        RagSettings.from_env(),
        enabled=True,
        db_dir=tmp_path / "rag_db",
        upload_dir=tmp_path / "rag_uploads",
        eval_dir=tmp_path / "rag_eval",
        judge_api_url="http://judge.invalid/v1",
        judge_api_key="test-key",
        judge_model="deepseek-v3",
        generator_model="qwen-plus",
        near_dup_threshold=0.9,
        judge_concurrency=2,
    )


import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def sqlite_session_factory(tmp_path):
    """用 SQLite 文件库代替 MySQL：DAO 只依赖会话工厂，与具体数据库无关。"""
    import backend.model.rag  # noqa: F401  注册表结构
    from backend.model import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'rag.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
