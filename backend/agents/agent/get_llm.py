import os

from langchain_openai import ChatOpenAI

from backend.core.config import load_env
from backend.core.single_tool import singleton_method

load_env()

model = os.getenv('MODEL_NAME','glm-5')
api_key = os.getenv('API_KEY')
base_url = os.getenv('API_URL')


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
