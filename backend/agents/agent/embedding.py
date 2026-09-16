"""
向量化模型（LlamaIndex Embedding）。

单独放一个模块，是为了让它不在服务启动路径上：`get_llm.py` 被 react_agent 导入，
而 llama-index 的导入要 1.5 秒、带进 1400 多个模块。记忆层改存 MySQL 之后，
当前没有任何在线请求需要 embedding，只有 RAG 知识库会用；由 RAG 在需要时再导入本模块。
"""
from typing import List
import os

from openai import OpenAI, AsyncOpenAI
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.bridge.pydantic import Field, PrivateAttr

from backend.core.config import load_env
from backend.core.single_tool import singleton_method

load_env()

api_key = os.getenv('API_KEY')
base_url = os.getenv('API_URL')
embedding_model = os.getenv('EMBEDDING_MODEL', 'qwen3-vl-embedding')


class DashScopeEmbedding(BaseEmbedding):
    """通过 DashScope OpenAI 兼容 /embeddings 接口实现的 LlamaIndex Embedding，绕过枚举校验。"""
    model_name: str = Field(default="qwen3-vl-embedding")
    _client: OpenAI = PrivateAttr()
    _aclient: AsyncOpenAI = PrivateAttr()

    def __init__(self, **data):
        super().__init__(**data)
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._aclient = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _get_query_embedding(self, query: str) -> List[float]:
        resp = self._client.embeddings.create(model=self.model_name, input=query)
        return resp.data[0].embedding

    def _get_text_embedding(self, text: str) -> List[float]:
        resp = self._client.embeddings.create(model=self.model_name, input=text)
        return resp.data[0].embedding

    async def _aget_query_embedding(self, query: str) -> List[float]:
        resp = await self._aclient.embeddings.create(model=self.model_name, input=query)
        return resp.data[0].embedding

    async def _aget_text_embedding(self, text: str) -> List[float]:
        resp = await self._aclient.embeddings.create(model=self.model_name, input=text)
        return resp.data[0].embedding


@singleton_method
def get_embedding_model() -> DashScopeEmbedding:
    return DashScopeEmbedding(
        model_name=embedding_model
    )
