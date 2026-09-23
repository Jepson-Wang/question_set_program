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
