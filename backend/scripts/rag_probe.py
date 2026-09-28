"""
一次性探针：在写检索与入库代码之前，用真实请求确认四件事。会产生极少量 API 费用。

    python -m backend.scripts.rag_probe      （在仓库根目录运行）

1. 重排接口：地址、模型名是否可用，响应结构长什么样（Task 14 的解析函数以此为准）
2. 向量化：LlamaIndex 的 embedding 封装能否拿到向量、维度多少
3. LlamaIndex + Chroma：查询分数与 cosine 距离的关系、重复 id 的写入行为（Task 2 的适配层以此为准）
4. 裁判模型：配置是否可用
"""
import asyncio
import json
import math
import tempfile

import httpx

from backend.agents.rag.config import RagSettings


async def probe_rerank(settings: RagSettings) -> None:
    """测重排接口的地址、模型名、响应结构；结果给 Task 14 的 parse_rerank_response。"""
    payload = {
        "model": settings.rerank_model,
        "input": {"query": "解一元一次方程", "documents": ["解方程 2x+3=7", "计算三角形的面积"]},
        "parameters": {"top_n": 2, "return_documents": False},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            settings.rerank_url,
            json=payload,
            headers={"Authorization": f"Bearer {settings.rerank_api_key}"},
        )
    print(f"[rerank] {settings.rerank_model} -> HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2)[:1500])
    print("[rerank] 预期：结果在 output.results 里，每项含 index 与 relevance_score，且方程那条分数更高")


async def probe_embedding(settings: RagSettings) -> None:
    """测 embedding 封装能否拿到向量、维度多少；维度给 Task 2 建 Chroma 集合时用。"""
    from backend.agents.agent.embedding import get_embedding_model

    vectors = await get_embedding_model().aget_text_embedding_batch(
        ["解方程 2x+3=7", "计算三角形的面积"]
    )
    print(f"[embedding] 返回 {len(vectors)} 条，维度 {len(vectors[0])}")
    print("[embedding] 预期：2 条，维度与 EMBEDDING_MODEL 的说明一致")


def probe_llamaindex_chroma() -> None:
    """测查询分数怎么换算成余弦相似度、重复 id 会不会覆盖；结果给 Task 2 的适配层。"""
    import chromadb
    from llama_index.core.schema import TextNode
    from llama_index.core.vector_stores.types import VectorStoreQuery
    from llama_index.vector_stores.chroma import ChromaVectorStore

    client = chromadb.PersistentClient(path=tempfile.mkdtemp())
    collection = client.get_or_create_collection("probe", metadata={"hnsw:space": "cosine"})
    store = ChromaVectorStore(chroma_collection=collection)
    store.add([
        TextNode(id_="same", text="a", embedding=[1.0, 0.0]),
        TextNode(id_="mid", text="b", embedding=[0.6, 0.8]),
    ])
    result = store.query(VectorStoreQuery(query_embedding=[1.0, 0.0], similarity_top_k=2))
    cosines = [round(1 + math.log(score), 4) for score in result.similarities]
    print(f"[chroma] ids={result.ids} 分数={[round(s, 4) for s in result.similarities]} "
          f"按 1+ln(分数) 换算={cosines}")
    print("[chroma] 预期：换算后 same 约 1.0、mid 约 0.6（LlamaIndex 返回的分数 = exp(-cosine 距离)）")

    store.add([TextNode(id_="same", text="changed", embedding=[1.0, 0.0])])
    print(f"[chroma] 对已有 id 再 add 一次后，原文={collection.get(ids=['same'])['documents']}")
    print("[chroma] 预期：仍是 ['a']（add 不覆盖已有记录，所以适配层的 upsert 要先删后加）")


async def probe_judge(settings: RagSettings) -> None:
    """测裁判模型的地址、Key、模型名能否调通；结果给 Task 8 的 QuestionJudge。"""
    from langchain_openai import ChatOpenAI

    if not (settings.judge_model and settings.judge_api_url and settings.judge_api_key):
        print("[judge] 未配置 JUDGE_MODEL / JUDGE_API_URL / JUDGE_API_KEY，跳过")
        return
    llm = ChatOpenAI(model=settings.judge_model, api_key=settings.judge_api_key,
                     base_url=settings.judge_api_url, temperature=0)
    response = await llm.ainvoke("只回答一个数字：方程 2x+3=7 中 x 等于几？")
    print(f"[judge] {settings.judge_model} -> {response.content!r}")


async def main() -> None:
    settings = RagSettings.from_env()
    for name, probe in (("rerank", probe_rerank),
                        ("embedding", probe_embedding),
                        ("judge", probe_judge)):
        try:
            await probe(settings)
        except Exception as e:
            print(f"[{name}] 失败：{type(e).__name__}: {e}")

    try:
        probe_llamaindex_chroma()
    except Exception as e:
        print(f"[chroma] 失败：{type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
