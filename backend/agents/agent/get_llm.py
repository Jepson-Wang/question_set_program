from typing import List

from backend.core.single_tool import singleton_method
from langchain_openai import ChatOpenAI
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.bridge.pydantic import Field
from openai import OpenAI, AsyncOpenAI
import os


model = os.getenv('MODEL_NAME')
api_key = os.getenv('API_KEY')
base_url = os.getenv('API_URL')
embedding_model = os.getenv('EMBEDDING_MODEL', 'text-embedding-v4')


@singleton_method
def get_llm(model: str = model, streaming: bool = False):
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        streaming=streaming
    )
    return llm


class DashScopeEmbedding(BaseEmbedding):
    """通过 DashScope OpenAI 兼容接口实现的 LlamaIndex Embedding，绕过枚举校验。"""
    model_name: str = Field(default="text-embedding-v4")
    api_key: str = Field(default="")
    api_base: str = Field(default="")

    def _get_client(self) -> OpenAI:
        return OpenAI(api_key=self.api_key, base_url=self.api_base)

    def _get_async_client(self) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=self.api_key, base_url=self.api_base)

    def _embed(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()
        response = client.embeddings.create(model=self.model_name, input=texts)
        return [item.embedding for item in response.data]

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._embed([query])[0]

    def _get_text_embedding(self, text: str) -> List[float]:
        return self._embed([text])[0]

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts)

    async def _aget_query_embedding(self, query: str) -> List[float]:
        client = self._get_async_client()
        response = await client.embeddings.create(model=self.model_name, input=[query])
        return response.data[0].embedding

    async def _aget_text_embedding(self, text: str) -> List[float]:
        client = self._get_async_client()
        response = await client.embeddings.create(model=self.model_name, input=[text])
        return response.data[0].embedding


@singleton_method
def get_embedding_model() -> DashScopeEmbedding:
    return DashScopeEmbedding(
        model_name=embedding_model,
        api_key=api_key,
        api_base=base_url,
    )
