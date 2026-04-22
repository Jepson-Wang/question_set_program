import asyncio
import os
from functools import partial

from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document
from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter
from llama_index.vector_stores.chroma import ChromaVectorStore

from dotenv import load_dotenv

from backend.agents.agent.get_llm import get_embedding_model
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

load_dotenv('.env')

COLLECTION_NAME = os.getenv('CHROMA_COLLECTION', 'vector_store_collection')
PERSIST_DIR = os.getenv('VECTOR_MEMORY_DIR', './vector_memory')
CHUNK_SIZE = int(os.getenv('CHROMA_CHUNK_SIZE', '512'))
CHUNK_OVERLAP = int(os.getenv('CHROMA_CHUNK_OVERLAP', '48'))


from backend.core.single_tool import singleMeta


class VectorStoreManager(metaclass=singleMeta):
    def __init__(self):
        os.makedirs(PERSIST_DIR, exist_ok=True)

        # 显式持有 embedding 模型实例（DashScope 封装）
        self.embed_model = get_embedding_model()

        # 初始化 Chroma 向量存储
        self.vector_store = ChromaVectorStore.from_params(
            collection_name=COLLECTION_NAME,
            persist_dir=PERSIST_DIR
        )
        self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)

        # 显式指定切分器：chunk_size / chunk_overlap 由环境变量控制，
        # 避免依赖 LlamaIndex 全局 Settings 的默认值(1024/20)
        self.node_parser = SentenceSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )

        # 缓存 index，整个生命周期复用，避免每次操作重建
        # 显式绑定 embed_model + transformations，不依赖全局 Settings
        self._index = VectorStoreIndex.from_vector_store(
            self.vector_store,
            storage_context=self.storage_context,
            embed_model=self.embed_model,
            transformations=[self.node_parser],
        )

    async def add_document(self, text: str, metadata: dict = None) -> bool:
        """将文本添加到向量库"""
        try:
            if metadata is None:
                metadata = {}
            document = Document(text=text, metadata=metadata)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, partial(self._index.insert, document))
            return True
        except Exception as e:
            logger.error("Error adding document: %s", e, exc_info=True)
            return False

    async def delete_document(self, doc_id: str) -> bool:
        """从向量库中删除文档"""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, partial(self.vector_store.delete, doc_id))
            return True
        except Exception as e:
            logger.error("Error deleting document: %s", e, exc_info=True)
            return False

    async def update_document(self, doc_id: str, text: str, metadata: dict = None) -> bool:
        """更新向量库中的文档（先删除再添加）"""
        if not await self.delete_document(doc_id):
            return False
        if metadata is None:
            metadata = {}
        document = Document(text=text, metadata=metadata, doc_id=doc_id)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, partial(self._index.insert, document))
            return True
        except Exception as e:
            logger.error("Error updating document: %s", e, exc_info=True)
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
            filters=filters,
            embed_model=self.embed_model
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, partial(query_engine.query, query_text)
        )
        return response
