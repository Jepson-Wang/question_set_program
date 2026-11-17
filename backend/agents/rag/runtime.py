"""
RAG 运行时：按需组装各组件的进程内单例。

组装失败的影响被限制在 RAG 自身：
- 检索侧（生题）由 integration.py 吞掉异常，生题照常进行
- 入库侧抛出明确的错误（如 JudgeConfigError），让调用方拒绝执行

所以这里不做「组装失败就降级」的兜底：入库的闸门一旦失灵，必须让调用方明确看到失败，
而不是悄悄放行未经复核的题目。
"""
from typing import Callable

from backend.agents.rag.config import RagSettings
from backend.agents.rag.ingest.generator import SeedGenerator
from backend.agents.rag.ingest.judge import QuestionJudge, build_judge_llm
from backend.agents.rag.ingest.service import IngestService, QuarantineFn
from backend.agents.rag.ingest.structurer import Structurer
from backend.agents.rag.store.lexical_index import LexicalIndex

# 两个集合分开存：题目库要能被检索链路频繁查询，教材库按知识点查、写入频率低。
# 混在一个集合里，教材讲解会挤占题目的召回名额。
QUESTION_COLLECTION = "rag_questions"
KNOWLEDGE_COLLECTION = "rag_knowledge"


class RagRuntime:
    def __init__(self, settings: RagSettings, *, embedder, question_store, knowledge_store,
                 lexical_index: LexicalIndex, llm_factory: Callable[[], object],
                 judge_factory: Callable[[], object], reranker=None):
        self.settings = settings
        self.embedder = embedder
        self.question_store = question_store
        self.knowledge_store = knowledge_store
        self.lexical_index = lexical_index
        self._llm_factory = llm_factory
        self._judge_factory = judge_factory
        self._reranker = reranker
        self._judge = None

    @classmethod
    def from_settings(cls, settings: RagSettings) -> "RagRuntime":
        """生产组装：LlamaIndex 向量化（DashScope）+ LlamaIndex 读写 Chroma + 独立配置的裁判。"""
        # 放在方法内部导入：LlamaIndex 与 chromadb 都很重，
        # 让没开 RAG 的进程不必为一次 import 付出几百毫秒。
        from backend.agents.agent.get_llm import get_llm
        from backend.agents.rag.store.embedder import LlamaIndexEmbedder
        from backend.agents.rag.store.llama_store import LlamaIndexVectorStore

        def judge_factory():
            # 裁判按自己的配置单独建：与生成模型同家族或没配 key 时，
            # build_judge_llm 会抛 JudgeConfigError，这正是入库前要拦住的错误。
            return QuestionJudge(build_judge_llm(settings), settings.judge_model, settings.judge_pass_score)

        return cls(
            settings,
            embedder=LlamaIndexEmbedder(),
            question_store=LlamaIndexVectorStore.from_chroma(settings.db_dir, QUESTION_COLLECTION),
            knowledge_store=LlamaIndexVectorStore.from_chroma(settings.db_dir, KNOWLEDGE_COLLECTION),
            lexical_index=LexicalIndex(),
            # 生成模型与设置同源：两者都取自环境变量 MODEL_NAME
            llm_factory=get_llm,
            judge_factory=judge_factory,
        )

    async def warm_up(self) -> int:
        """从向量库重建词法索引，返回题目数。服务启动、命令行入库前各调一次。"""
        # 词法索引不能增量更新，只能从向量库整体重建：命令行入库后新题才会被检索到。
        return await self.lexical_index.rebuild_from_store(self.question_store)

    def ensure_judge(self):
        """构建并缓存裁判；配置不对时抛 JudgeConfigError。后台接口在创建任务前用它做预检。"""
        # 只构建一次：裁判是复核闸门，同一份配置反复构建既慢又可能中途换模型，
        # 让同一批题的复核标准前后不一致。
        if self._judge is None:
            self._judge = self._judge_factory()
        return self._judge

    def ingest_service(self, quarantine: QuarantineFn | None, require_judge: bool = True) -> IngestService:
        """require_judge=False 只用于人工复核通过（approve），它本来就不经过裁判。"""
        llm = self._llm_factory()
        return IngestService(
            settings=self.settings,
            embedder=self.embedder,
            question_store=self.question_store,
            knowledge_store=self.knowledge_store,
            lexical_index=self.lexical_index,
            structurer=Structurer(llm),
            generator=SeedGenerator(llm),
            judge=self.ensure_judge() if require_judge else None,
            quarantine=quarantine,
        )


_runtime: RagRuntime | None = None


def get_rag_runtime() -> RagRuntime:
    global _runtime
    if _runtime is None:
        # 懒加载：只有真正要用 RAG 的地方才会付出组装成本（建 Chroma 集合要落盘）
        _runtime = RagRuntime.from_settings(RagSettings.from_env())
    return _runtime


def set_rag_runtime(runtime: RagRuntime | None) -> None:
    """测试注入用；传 None 恢复为按配置懒加载。"""
    global _runtime
    _runtime = runtime
