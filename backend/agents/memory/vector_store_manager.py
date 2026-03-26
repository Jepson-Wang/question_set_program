import os

from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.schema import Document
from dotenv import load_dotenv
load_dotenv('.env')

COLLECTION_NAME = os.getenv('CHROMA_COLLECTION', 'default_collection')
PERSIST_DIR = os.getenv('CHROMA_PERSIST_DIR', './chroma_db')

from backend.core.single_tool import singleMeta


class VectorStoreManager(metaclass=singleMeta):
    def __init__(self):
        # 确保持久化目录存在
        os.makedirs(PERSIST_DIR, exist_ok=True)

        # 使用本地文件存储模式，不使用HttpClient
        self.vector_store = ChromaVectorStore.from_params(
            collection_name=COLLECTION_NAME,
            persist_dir=PERSIST_DIR
        )
        self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)

    async def add_document(self, text, metadata=None) -> bool:
        """Add a document to the vector store"""
        if metadata is None:
            metadata = {}
        document = Document(text=text, metadata=metadata)
        index = VectorStoreIndex.from_documents([document], storage_context=self.storage_context)
        # 持久化到磁盘
        self.vector_store.persist()
        return True

    async def delete_document(self, doc_id) -> bool:
        """Delete a document from the vector store"""
        try:
            self.vector_store.delete(doc_id)
            # 持久化到磁盘
            self.vector_store.persist()
            return True
        except Exception as e:
            print(f"Error deleting document: {e}")
            return False

    async def update_document(self, doc_id, text, metadata=None) -> bool:
        """Update a document in the vector store"""
        # 对于 Chroma，先删除再重新添加
        if await self.delete_document(doc_id):
            if metadata is None:
                metadata = {}
            document = Document(text=text, metadata=metadata, doc_id=doc_id)
            index = VectorStoreIndex.from_documents([document], storage_context=self.storage_context)
            # 持久化到磁盘
            self.vector_store.persist()
            return True
        return False

    async def query(self, query_text, filter=None, top_k=5):
        """Query the vector store with optional metadata filter"""
        index = VectorStoreIndex.from_vector_store(self.vector_store)
        query_engine = index.as_query_engine(
            similarity_top_k=top_k,
            filters=filter
        )
        response = query_engine.query(query_text)
        return response