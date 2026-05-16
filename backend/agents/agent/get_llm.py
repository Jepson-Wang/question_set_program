from typing import List
import os

from openai import OpenAI, AsyncOpenAI
from langchain_openai import ChatOpenAI
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.bridge.pydantic import Field, PrivateAttr

from backend.core.single_tool import singleton_method


model = os.getenv('MODEL_NAME','glm-5')
api_key = os.getenv('API_KEY')
base_url = os.getenv('API_URL')
embedding_model = os.getenv('EMBEDDING_MODEL', 'qwen3-vl-embedding')


@singleton_method
def get_llm(model: str = model, streaming: bool = False):
    llm = ChatOpenAI(
        model=model,
        api_key=api_key, #type:ignore
        base_url=base_url,
        temperature=0.3,
        streaming=streaming
    )
    return llm


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
