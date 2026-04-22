from backend.core.single_tool import singleton_method
from langchain_openai import ChatOpenAI
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.bridge.pydantic import Field
import os


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
    """通过 DashScope OpenAI 兼容接口实现的 LlamaIndex Embedding，绕过枚举校验。"""
    model_name: str = Field(default="qwen3-vl-embedding")

    def _get_client(self) -> ChatOpenAI:
        return get_llm(self.model_name)

    def _get_async_client(self) -> ChatOpenAI:
        return get_llm(self.model_name, streaming=True)


@singleton_method
def get_embedding_model() -> DashScopeEmbedding:
    return DashScopeEmbedding(
        model_name=embedding_model
    )
