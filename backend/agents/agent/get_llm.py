from backend.core.single_tool import singleton_method
from langchain_openai import ChatOpenAI
from llama_index.embeddings.openai import OpenAIEmbedding
import os


model = os.getenv('MODEL_NAME')
api_key = os.getenv('API_KEY')
base_url = os.getenv('API_URL')
embedding_model = os.getenv('EMBEDDING_MODEL', 'text-embedding-v4')



@singleton_method
def get_llm(model:str=model, streaming:bool=False):
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        streaming=streaming
    )
    return llm

@singleton_method
def get_embedding_model() -> OpenAIEmbedding:
    model = OpenAIEmbedding(
        model=embedding_model,
        api_key=api_key,
        api_base=base_url,
    )
    return model
