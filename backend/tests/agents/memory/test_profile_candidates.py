"""
画像候选池的行为测试，跑在 Redis 测试库（db 15）上。

这里只测候选池自己的职责——「这个偏好够不够格进画像」。它全程只碰 Redis，
不认识 user_profile 表，所以**不测落库**：写库是 user_profile_save_tool 的活，
那部分归 tests/agents/tools/ 下的测试管。

TTL 一律用 field 级（HSETEX，Redis 8.0+）。两条写路径语义不同，都要守住：
- 创建：ex=field_ttl，窗口从这一刻起算
- 更新：keepttl=True，保住原有到期时刻，不续命
"""
import asyncio

import pytest

from backend.agents.memory.profile_candidates import ProfileCandidate

USER_A = 100
USER_B = 200

FIELD, SUB_KEY = "preferences", "题目风格"
# 不手写 "preferences.题目风格"：拼接规则改了测试要跟着走，
# 而不是变成一个查不到东西却又不报错的断言
FIELD_PATH = ProfileCandidate.generate_field_key(FIELD, SUB_KEY)


@pytest.fixture
def make_store(redis_test_client):
    """
    造一个指向测试库的候选池，各项配置按需覆盖。

    全部走构造参数，不碰任何私有属性：名字写错会当场 TypeError，
    而 setattr 写错只会凭空新建一个没人读的属性，测试静默连到生产库。
    """
    def _make(**kwargs) -> ProfileCandidate:
        return ProfileCandidate(client=redis_test_client, **kwargs)
    return _make


@pytest.fixture
def store(make_store) -> ProfileCandidate:
    return make_store()


async def test_first_offer_does_not_promote(store):
    """第一次提交只记候选，不晋升——这正是频次确认存在的理由"""
    times, promoted = await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    assert times == 1
    assert promoted is False


async def test_second_offer_promotes(store):
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    times, promoted = await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    assert times == 2
    assert promoted is True


async def test_candidates_are_isolated_per_user(store):
    """两个用户提交完全相同的字段路径，计数必须各算各的"""
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    await store.submit_one_candidate(USER_B, FIELD, SUB_KEY, "简单点b")

    a = await store.query_candidates_by_user(USER_A)
    b = await store.query_candidates_by_user(USER_B)

    assert len(a) == 1 and len(b) == 1
    assert a[FIELD_PATH] == {"value": "简单点a", "times": 1}
    assert b[FIELD_PATH] == {"value": "简单点b", "times": 1}


async def test_promotion_keeps_candidate_until_confirmed(store):
    """
    晋升不删候选：要等调用方落库成功、再调 delete_field_by_given 才删。
    先删后落库的话，落库一失败，这条已经确认过的偏好就永久丢了。
    """
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    _, promoted = await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    assert promoted is True
    assert await store.query_candidates_by_user(USER_A) == {
        FIELD_PATH: {"value": "简单点a", "times": 2}
    }


async def test_confirmed_field_is_removed_and_restarts_from_one(store):
    """
    确认删除之后再提交是全新的一轮计数——候选池不记「谁晋升过」，
    因为用户改主意时那个键必须还能再晋升一次来覆盖旧值。
    """
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    await store.delete_field_by_given(USER_A, [(FIELD, SUB_KEY)])
    assert await store.query_candidates_by_user(USER_A) == {}

    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "难一点")
    assert await store.query_candidates_by_user(USER_A) == {
        FIELD_PATH: {"value": "难一点", "times": 1}
    }


async def test_unconfirmed_promotion_promotes_again_next_time(store):
    """
    落库失败时调用方不会确认删除，候选的计数停在阈值上。
    用户下次再提到时必须还能晋升，这是落库失败后唯一的重试通道。
    """
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    times, promoted = await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    assert times == 3
    assert promoted is True


async def test_delete_only_removes_given_fields(store):
    """只删这次晋升的子键，同一用户还在计数的候选不能被连带删掉"""
    other = "讲解详细度"
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    await store.submit_one_candidate(USER_A, FIELD, other, "详细")

    await store.delete_field_by_given(USER_A, [(FIELD, SUB_KEY)])

    assert await store.query_candidates_by_user(USER_A) == {
        ProfileCandidate.generate_field_key(FIELD, other): {"value": "详细", "times": 1}
    }


async def test_delete_with_empty_list_is_a_no_op(store):
    """空列表直接返回；真发一条不带 field 的 HDEL，Redis 会报参数个数错误"""
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    await store.delete_field_by_given(USER_A, [])
    assert len(await store.query_candidates_by_user(USER_A)) == 1


async def test_ttl_is_not_refreshed_on_update(make_store, redis_test_client):
    """
    更新只改值和计数，不重置到期时刻。

    刷新的话，「7 天」就不再是「两次提及的时间窗口」——用户第 1 天提一次、
    第 400 天再提一次照样晋升，隔了一年多的两次被当成稳定偏好。

    阈值调到 3，让两次提交都停在晋升之前，只测「更新」这一条写路径本身。
    """
    store = make_store(field_ttl=100, times_to_submit=3)
    key = ProfileCandidate.key(USER_A)

    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    # 这组命令都支持多 field，所以返回的是列表，取 [0]
    before = (await redis_test_client.hpttl(key, FIELD_PATH))[0]
    assert before > 0

    await asyncio.sleep(0.1)
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点b")
    after = (await redis_test_client.hpttl(key, FIELD_PATH))[0]

    assert after <= before, "更新不应该重置到期时刻；被刷新的话 after 会跳回 100000 附近"
    assert (await store.query_candidates_by_user(USER_A))[FIELD_PATH] == {
        "value": "简单点b", "times": 2
    }


async def test_field_disappears_after_ttl(make_store):
    """
    到期后候选项自动消失，不需要谁来清理。

    睡 1.3 秒而不是刚好 1 秒：Redis 是惰性删除 + 定期扫描，不会在到点那一刻
    消失。实测 ttl=2 时 2.01s 字段还在、2.06s 才没，卡边界会变成 flaky 测试。
    """
    store = make_store(field_ttl=1)
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    assert len(await store.query_candidates_by_user(USER_A)) == 1

    await asyncio.sleep(1.3)
    assert await store.query_candidates_by_user(USER_A) == {}
