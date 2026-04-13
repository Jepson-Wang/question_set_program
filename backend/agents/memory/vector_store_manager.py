import asyncio
import os
from functools import partial

from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.core.schema import Document
from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter
from llama_index.vector_stores.chroma import ChromaVectorStore

from dotenv import load_dotenv

from backend.agents.agent.get_llm import get_embedding_model

load_dotenv('.env')

COLLECTION_NAME = os.getenv('CHROMA_COLLECTION', 'default_collection')
PERSIST_DIR = os.getenv('CHROMA_PERSIST_DIR', './chroma_db')


from backend.core.single_tool import singleMeta


class VectorStoreManager(metaclass=singleMeta):
    def __init__(self):
        os.makedirs(PERSIST_DIR, exist_ok=True)

        # 配置 DashScope Embedding（通过 OpenAI 兼容接口）
        embed_model = get_embedding_model()
        Settings.embed_model = embed_model

        # 初始化 Chroma 向量存储
        self.vector_store = ChromaVectorStore.from_params(
            collection_name=COLLECTION_NAME,
            persist_dir=PERSIST_DIR
        )
        self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)

        # 缓存 index，整个生命周期复用，避免每次操作重建
        self._index = VectorStoreIndex.from_vector_store(
            self.vector_store,
            storage_context=self.storage_context
        )

    async def add_document(self, text: str, metadata: dict = None) -> bool:
        """将文本添加到向量库"""
        try:
            if metadata is None:
                metadata = {}
            document = Document(text=text, metadata=metadata)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, partial(self._index.insert, document))
            return True
        except Exception as e:
            print(f"Error adding document: {e}")
            return False

    async def delete_document(self, doc_id: str) -> bool:
        """从向量库中删除文档"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, partial(self.vector_store.delete, doc_id))
            return True
        except Exception as e:
            print(f"Error deleting document: {e}")
            return False

    async def update_document(self, doc_id: str, text: str, metadata: dict = None) -> bool:
        """更新向量库中的文档（先删除再添加）"""
        if not await self.delete_document(doc_id):
            return False
        if metadata is None:
            metadata = {}
        document = Document(text=text, metadata=metadata, doc_id=doc_id)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, partial(self._index.insert, document))
            return True
        except Exception as e:
            print(f"Error updating document: {e}")
            return False

    async def query(self, query_text: str, user_id: int = None, top_k: int = 5):
        """查询向量库，可按 user_id 过滤结果"""
        filters = None
        if user_id is not None:
            filters = MetadataFilters(
                filters=[ExactMatchFilter(key="user_id", value=user_id)]
            )

        query_engine = self._index.as_query_engine(
            similarity_top_k=top_k,
            filters=filters
        )
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, partial(query_engine.query, query_text)
        )
        return response
