from backend.agents.rag.models import JudgeResult, QuestionItem
from backend.dao.rag_mapper import RagJobMapper, RagQuarantineMapper


async def test_job_lifecycle(sqlite_session_factory):
    jobs = RagJobMapper(sqlite_session_factory)
    job_id = await jobs.create("file", source="教材.pdf", params={"grade": "七年级"}, created_by=1)

    job = await jobs.get(job_id)
    assert job["status"] == "pending" and job["params"] == {"grade": "七年级"}

    await jobs.mark_running(job_id)
    await jobs.mark_succeeded(job_id, {"passed": 3})
    job = await jobs.get(job_id)
    assert job["status"] == "succeeded" and job["report"] == {"passed": 3}


async def test_unknown_job_is_none(sqlite_session_factory):
    assert await RagJobMapper(sqlite_session_factory).get("nope") is None


async def test_fail_interrupted_only_touches_unfinished_jobs(sqlite_session_factory):
    jobs = RagJobMapper(sqlite_session_factory)
    pending, running, done = [await jobs.create("file", source=s) for s in ("a", "b", "c")]
    await jobs.mark_running(running)
    await jobs.mark_succeeded(done, {})

    assert await jobs.fail_interrupted() == 2
    assert (await jobs.get(pending))["status"] == "failed"
    assert "重新提交" in (await jobs.get(running))["error"]
    assert (await jobs.get(done))["status"] == "succeeded"


def _item():
    return QuestionItem(stem="解方程 2x+3=7", answer="x=3", knowledge_points=["一元一次方程"])


async def test_quarantine_put_list_and_review(sqlite_session_factory):
    q = RagQuarantineMapper(sqlite_session_factory)
    entry_id = await q.put(
        _item(), JudgeResult(False, 0.2, ["答案错误"], judge_answer="x=2", judge_model="deepseek-v3"),
        job_id="job1",
    )

    [entry] = await q.list(status="pending")
    assert entry["id"] == entry_id
    assert entry["item"]["stem"] == "解方程 2x+3=7"
    assert entry["judge"]["reasons"] == ["答案错误"] and entry["job_id"] == "job1"

    assert await q.set_status(entry_id, "approved", reviewer_id=7) is True
    assert await q.list(status="pending") == []
    assert (await q.get(entry_id))["status"] == "approved"


async def test_review_is_single_shot(sqlite_session_factory):
    q = RagQuarantineMapper(sqlite_session_factory)
    entry_id = await q.put(_item(), JudgeResult(False, 0.1))
    assert await q.set_status(entry_id, "discarded", reviewer_id=1) is True
    assert await q.set_status(entry_id, "approved", reviewer_id=1) is False, "已处理过的条目不能再改状态"
    assert await q.set_status(99999, "approved", reviewer_id=1) is False
