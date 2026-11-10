"""
画像候选池的行为测试，跑在 Redis 测试库（db 15）上。

这里只测候选池自己的职责——「这个偏好够不够格进画像」。它全程只碰 Redis，
不认识 user_profile 表，所以**不测落库**：写库是 user_profile_save_tool 的活，
那部分归 tests/agents/tools/ 下的测试管。

TTL 一律用 field 级（HSETEX，Redis 8.0+）。两条写路径语义不同，都要守住：
- 计数（重新）从 1 起：创建、值变了、脏数据，都是 EX field_ttl，窗口从这一刻重开
- 计数 +1（同值再提）：PX 剩余时间 + ttl_extend，在剩余窗口上续命，没有上限
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
    await store.delete_field_by_given(USER_A, [(FIELD, SUB_KEY, "简单点a")])
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

    await store.delete_field_by_given(USER_A, [(FIELD, SUB_KEY, "简单点a")])

    assert await store.query_candidates_by_user(USER_A) == {
        ProfileCandidate.generate_field_key(FIELD, other): {"value": "详细", "times": 1}
    }


async def test_delete_with_empty_list_is_a_no_op(store):
    """空列表直接返回；真发一条不带 field 的 HDEL，Redis 会报参数个数错误"""
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    await store.delete_field_by_given(USER_A, [])
    assert len(await store.query_candidates_by_user(USER_A)) == 1


class _BarrierClient:
    """
    包一层真实客户端：每次 hget 拿到结果后，要等所有调用方都读完才返回。

    把「两个请求都先读、再各自写」这个最坏的交错固定下来，竞态就从「碰巧」变成「必然」。
    其他命令原样转发。
    """
    def __init__(self, client, parties: int = 2):
        self._client = client
        self._barrier = asyncio.Barrier(parties)

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def hget(self, *args, **kwargs):
        value = await self._client.hget(*args, **kwargs)
        async with asyncio.timeout(5):  # 只有一方到达时别永远卡住
            await self._barrier.wait()
        return value


async def test_concurrent_submits_do_not_lose_count(make_store, redis_test_client):
    """
    已经提交过 1 次，又有两个请求同时提交同一个子键：计数必须是 3。

    现在能过，是因为 Python 侧不再 HGET：读-算-写整个收进一个 Lua 脚本，Redis 串行执行，
    两个请求不可能交错（屏障只拦 hget，现在没有 hget 可拦）。改造前这条必然失败：
    屏障把两个请求都卡在 HGET 之后，都读到 1、都写回 2，少计一次。
    阈值是 2 时，旧实现的后果是两个请求都晋升、重复落库（由幂等合并消化）；
    阈值更高时，后果是偏好晚一轮才能晋升。
    """
    await make_store().submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")

    # 阈值调高，只看计数本身，不让晋升逻辑掺进来
    racing = ProfileCandidate(client=_BarrierClient(redis_test_client), times_to_submit=10)
    await asyncio.gather(
        racing.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点"),
        racing.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点"),
    )

    stored = await make_store().query_candidates_by_user(USER_A)
    assert stored[FIELD_PATH]["times"] == 3


async def test_default_durations_are_one_day_and_twelve_hours(redis_test_client):
    """默认窗口 1 天、每次再提及续 12 小时；改默认值要连同产品语义一起改，所以钉住"""
    store = ProfileCandidate(client=redis_test_client)
    assert store.field_ttl == 86400
    assert store.ttl_extend == 43200


async def test_created_field_gets_full_ttl(make_store, redis_test_client):
    """新建的候选从这一刻起拿到完整的初始窗口，不多不少"""
    store = make_store(field_ttl=100, ttl_extend=50)
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    ttl = (await redis_test_client.hpttl(ProfileCandidate.key(USER_A), FIELD_PATH))[0]
    assert 100_000 - 1000 < ttl <= 100_000


async def test_same_value_update_extends_remaining_ttl(make_store, redis_test_client):
    """
    同值再提一次，在剩余时间上加 ttl_extend，而不是重置回初始窗口。

    一个反复被提起的偏好应该活得更久；但续的是「剩余 + 增量」，
    所以窗口只会随真实的再次提及增长，不会因为提及一次就被打回满额。

    阈值调到 3，让两次提交都停在晋升之前，只测「计数 +1」这一条写路径本身。
    """
    store = make_store(field_ttl=100, ttl_extend=50, times_to_submit=3)
    key = ProfileCandidate.key(USER_A)

    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    # 这组命令都支持多 field，所以返回的是列表，取 [0]
    before = (await redis_test_client.hpttl(key, FIELD_PATH))[0]
    assert before > 0

    await asyncio.sleep(0.1)
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    after = (await redis_test_client.hpttl(key, FIELD_PATH))[0]

    assert after > before
    assert before + 50_000 - 1000 <= after <= before + 50_000
    assert (await store.query_candidates_by_user(USER_A))[FIELD_PATH] == {
        "value": "简单点a", "times": 2
    }


async def test_value_change_restarts_the_window(make_store, redis_test_client):
    """
    值变了：计数归 1，窗口也跟着回到初始长度。

    计数和窗口一起重开——新主张不该继承旧主张快过期（或被续得很长）的窗口。
    先把窗口续到 ~150s，换值后必须回到 <= 100s，才能证明是重置而不是沿用。
    """
    store = make_store(field_ttl=100, ttl_extend=50, times_to_submit=10)
    key = ProfileCandidate.key(USER_A)

    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    await asyncio.sleep(0.1)
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")
    extended = (await redis_test_client.hpttl(key, FIELD_PATH))[0]
    assert extended > 100_000

    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "难一点")
    after = (await redis_test_client.hpttl(key, FIELD_PATH))[0]

    assert 90_000 < after <= 100_000
    assert (await store.query_candidates_by_user(USER_A))[FIELD_PATH] == {
        "value": "难一点", "times": 1
    }


async def test_corrupt_record_without_ttl_gets_a_window(make_store, redis_test_client):
    """
    手写进去的脏数据没有 TTL，不处理的话会永远留在池里。
    它被当作计数从 1 起，所以这次写入顺带补上初始窗口。
    """
    store = make_store(field_ttl=100, ttl_extend=50)
    key = ProfileCandidate.key(USER_A)
    await redis_test_client.hset(key, FIELD_PATH, "not json")
    assert (await redis_test_client.hpttl(key, FIELD_PATH))[0] == -1

    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点a")

    ttl = (await redis_test_client.hpttl(key, FIELD_PATH))[0]
    assert 0 < ttl <= 100_000


async def test_value_change_resets_count_and_does_not_promote(store):
    """
    B 语义：先说「简单点」再说「难一点」是两个主张，不能凑成一次确认。
    计数归 1、不晋升，池子里存的是新值。
    """
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    times, promoted = await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "难一点")

    assert (times, promoted) == (1, False)
    assert (await store.query_candidates_by_user(USER_A))[FIELD_PATH] == {
        "value": "难一点", "times": 1
    }


async def test_same_new_value_after_reset_promotes(store):
    """重置之后新主张重新累计：同一个新值再来一次，照常到阈值晋升"""
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "难一点")
    times, promoted = await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "难一点")

    assert (times, promoted) == (2, True)


@pytest.mark.parametrize("garbage", ["not json", "123", '"text"', '{"value": 1, "times": "x"}'])
async def test_corrupt_record_self_heals(store, redis_test_client, garbage):
    """
    脏数据（非 JSON、JSON 但不是对象、字段类型不对）不能让这个用户的候选池永久写不进去：
    当作没有旧状态，从 1 重新计，这次写入就把它修好，且不抛异常。
    """
    key = ProfileCandidate.key(USER_A)
    await redis_test_client.hset(key, FIELD_PATH, garbage)

    times, promoted = await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")

    assert (times, promoted) == (1, False)
    assert (await store.query_candidates_by_user(USER_A))[FIELD_PATH] == {
        "value": "简单点", "times": 1
    }


async def test_threshold_comes_from_the_instance(make_store):
    """阈值是实例配置，不是写死在脚本里的常量：配成 3 就要第三次才晋升"""
    store = make_store(times_to_submit=3)
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    _, second = await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    times, third = await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")

    assert second is False
    assert (times, third) == (3, True)


async def test_confirm_delete_removes_matching_value(store):
    """晋升时的值和存着的值一致：正常删除"""
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")

    await store.delete_field_by_given(USER_A, [(FIELD, SUB_KEY, "简单点")])

    assert await store.query_candidates_by_user(USER_A) == {}


async def test_confirm_delete_keeps_candidate_changed_after_promotion(store):
    """
    晋升之后、确认删除之前，别的请求把同一子键改成了新值（计数重置为 1）。
    这时删除要跳过它——盲删会把新主张刚起步的计数一起抹掉。
    """
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")  # 晋升了「简单点」
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "难一点")  # 并发的新主张

    await store.delete_field_by_given(USER_A, [(FIELD, SUB_KEY, "简单点")])

    assert await store.query_candidates_by_user(USER_A) == {
        FIELD_PATH: {"value": "难一点", "times": 1}
    }


async def test_confirm_delete_only_removes_matching_ones_in_a_mixed_list(store):
    """一批里有的值对得上、有的对不上：只删对得上的"""
    other = "讲解详细度"
    other_path = ProfileCandidate.generate_field_key(FIELD, other)
    await store.submit_one_candidate(USER_A, FIELD, SUB_KEY, "简单点")
    await store.submit_one_candidate(USER_A, FIELD, other, "详细")

    await store.delete_field_by_given(
        USER_A, [(FIELD, SUB_KEY, "简单点"), (FIELD, other, "简略")]
    )

    assert await store.query_candidates_by_user(USER_A) == {
        other_path: {"value": "详细", "times": 1}
    }


async def test_confirm_delete_of_missing_field_does_not_raise(store):
    """候选已经过期或被删：什么都不删，也不抛异常（落库已经成功，不能让调用方误报失败）"""
    await store.delete_field_by_given(USER_A, [(FIELD, SUB_KEY, "简单点")])
    assert await store.query_candidates_by_user(USER_A) == {}


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
