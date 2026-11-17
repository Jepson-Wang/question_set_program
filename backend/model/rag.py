from sqlalchemy import JSON, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from backend.model import Base


class RagIngestJob(Base):
    """
    入库任务：后台接口与命令行共用，记录状态与入库报告。

    单独一张表而不是挂在会话上：入库是异步跑的，可能跨越一次对话甚至一次重启，
    任务的生命周期比请求长。只有任务这一张表能同时回答「现在还有什么没跑完」
    和「上次那批入库结果如何」。
    """
    __tablename__ = "rag_ingest_job"

    id = Column(String(32), primary_key=True, comment="任务 id（uuid4 hex）")
    kind = Column(String(16), nullable=False, comment="file / generate")
    status = Column(String(16), nullable=False, default="pending",
                    comment="pending / running / succeeded / failed")
    source = Column(String(255), nullable=False, default="", comment="原始文件名或生成参数摘要")
    params = Column(JSON, nullable=True, comment="年级提示、生成参数等")
    report = Column(JSON, nullable=True, comment="IngestReport")
    error = Column(Text, nullable=True)
    created_by = Column(Integer, nullable=True, comment="发起任务的用户 id")
    create_time = Column(DateTime, default=func.current_timestamp())
    # onupdate 只在应用层没显式给值时兜底；DAO 每次更新都会显式写 update_time
    update_time = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())


class RagQuarantine(Base):
    """
    复核未通过的题目，等待人工处理。放 MySQL 而不是向量库：它是工作流状态，不是检索数据。

    裁判没通过的题不能直接进检索库，但也不能丢——人工复核通过后仍可能是好题。
    所以先落这张表，由管理员决定放行还是丢弃。
    """
    __tablename__ = "rag_quarantine"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(String(40), nullable=False, index=True)
    payload = Column(JSON, nullable=False, comment="QuestionItem 的全部字段")
    judge = Column(JSON, nullable=False, comment="JudgeResult")
    status = Column(String(16), nullable=False, default="pending",
                    comment="pending / approved / discarded")
    job_id = Column(String(32), nullable=True)
    reviewed_by = Column(Integer, nullable=True)
    create_time = Column(DateTime, default=func.current_timestamp())
    review_time = Column(DateTime, nullable=True)
