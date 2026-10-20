"""
用于记录某个用户画像的偏好被统计几次，如果超过阈值，那么就加入到库中
"""
import json
import os

from redis import RedisError

from backend.core.config import load_env
from backend.middleware.logging import get_logger
from backend.utils.redis_client import get_redis_client

# 开发选择使用 Redis hash，如果存在本地的话，
# 如果这个进程被强杀了，会导致数据丢失
# Redis Hash 的结构应该是 key = profile:candidates:user_id
# field = 字段名.子键    value = 当前值 + 累计次数

"""
不同字段需要不同的解决方案：
事实类：grade subject，第一次就落库
自由观察类：notes 第一次久罗库
偏好类：weak_points，preferences 进候选池，同一个字段出现两次才落库
但这会导致偏好类的用户响应比较缓慢甚至不生效,而且同一字段不一定同时生效两次
为什么偏好类需要延迟落库：因为一次性落库，就会导致后续的每一次请求都向这个profile来落
"""

# 把 submit_one_candidate 改写成 Lua 脚本时（Task 9.5 Step 2）要防的几类失败：
# 1. Lua 运行期错误：nil 参与运算、函数名拼错（cjson.decod）、索引 nil，最常见
# 2. redis.call 返回错误：WRONGTYPE（key 不是 hash）、参数个数不对。redis.call 遇错
#    会中断整个脚本并把错误抛回客户端，要兜住得用 redis.pcall
# 3. OOM：生产 Redis 的 maxmemory-policy 是 noeviction，内存满了写命令直接报错
# 4. 脚本超时：超过 busy-reply-threshold（Redis 7 之前叫 lua-time-limit，默认 5000ms）
#    后，Redis 不会中断脚本，只是对其他客户端回 BUSY。脚本还没写过数据时可以 SCRIPT KILL；
#    写过之后会被拒绝（杀掉会留下无法回滚的半截状态），只剩 SHUTDOWN NOSAVE，
#    丢掉上次持久化之后的全部数据
# 所以脚本必须短，而且写操作放在最后：写之前的那段时间里，脚本都还能被杀掉

load_env()

logger = get_logger(__name__)

PRE_KEY = "profile:candidates:"
DEFAULT_FIELD_TTL = 604800
DEFAULT_TIMES_TO_SUBMIT = 2


def _read_times_to_submit() -> int:
    """
    从 PROFILE_PROMOTE_THRESHOLD 读晋升阈值。

    配错时回落到默认值而不是让服务起不来：这只是个调参开关，和 JWT_SECRET_KEY
    那种「缺了就必须 fail fast」的密钥不同——为了一个阈值写错就拒绝启动，
    代价远大于收益。但两种异常情况都要记 error，否则配置没生效没人会发现。
    """
    raw = os.getenv("PROFILE_PROMOTE_THRESHOLD", "").strip()
    if not raw:
        return DEFAULT_TIMES_TO_SUBMIT

    try:
        times = int(raw)
    except ValueError:
        logger.error(
            "PROFILE_PROMOTE_THRESHOLD=%r 不是整数，已回落到默认值 %s",
            raw, DEFAULT_TIMES_TO_SUBMIT,
        )
        return DEFAULT_TIMES_TO_SUBMIT

    if times < 2:
        # 阈值小于 2 等于「第一次提交就晋升」，频次确认整个失效，行为退回到引入
        # 候选池之前。允许这么配（演示时想要即时反馈），但必须喊一声——
        # 否则配错和故意配成同一个样子，谁也分不出来。
        logger.error(
            "PROFILE_PROMOTE_THRESHOLD=%s 小于 2，频次确认已关闭："
            "偏好第一次出现就会写入长期画像",
            times,
        )
    return times


TIMES_TO_SUBMIT = _read_times_to_submit()


class ProfileCandidate:

    def __init__(self, client=None,
                 field_ttl: int = DEFAULT_FIELD_TTL,
                 times_to_submit: int = TIMES_TO_SUBMIT):
        """
        :param client: Redis 客户端，不传则取全局实例。留这个口子是为了测试能注入
            指向测试库的客户端——参数名写错会当场 TypeError，而事后 setattr 换
            私有属性写错了只会静默新建一个没人读的字段，测试照跑但连错了库
        """
        self.field_ttl = field_ttl
        self._client = client or get_redis_client().client
        self.times_to_submit = times_to_submit

    async def submit_one_candidate(self,user_id: int,field:str, sub_key: str, value: str) -> tuple[int,bool]:
        """
        提交一个候选人到 redis 库中, 如果 field 达到落库要求了，就返回 true
        field只接受str,如果为list[str]，请循环调用
        """
        # 这个里面需要完成：拼接key，将用户的相关candidate查出来，如果存在，那就incr，否则就为1
        # 还需要完成更新field的步骤
        # 需要的value: user_id, field,value
        # 构造 query_key，用于后续 query 拿到 field-value
        query_key = ProfileCandidate.key(user_id)
        query_field = ProfileCandidate.generate_field_key(field,sub_key)

        try:
            # 判断是否含有这个field，如果没有就创建一个，否则就incr
            query = await self._client.hget(query_key, query_field)
            if not query:
                final_res = {
                    "value": value,
                    "times": 1,
                }
                await self._client.hsetex(query_key,query_field,json.dumps(final_res),ex=self.field_ttl)
                return int(final_res["times"]),False
            else:
                try:
                    final_res = json.loads(query)
                except json.JSONDecodeError as e:
                    logger.error("submit_one_candidate 序列化 %s 失败: %s",query,e)
                    final_res = {
                        "value": value,
                        "times": 0,
                    }
                # 同键冲突以新值为准：用户先说「简单点」又说「难一点」，晋升时
                # 该写进画像的是后者。只加计数不换值的话，用户的修正会被静默吞掉
                final_res["value"] = value
                final_res["times"] += 1

                # 先将当前结果缓存到Redis中，防止落库失败 times 没更新
                await self._client.hsetex(query_key, query_field, json.dumps(final_res), keepttl=True)

                if final_res["times"] >= self.times_to_submit:
                    # HDEL 操作更改为一个独立的方法，在落库成功后调用
                    # 当前如果 times 达到标准了，就直接通知上层
                    return int(final_res["times"]), True

                return int(final_res["times"]), False

        except RedisError as e:
            # 只降级 Redis 故障：候选池写不进去不该拖垮整个请求。
            # 编程错误不在这里捕获，让它抛出去，否则 bug 会被伪装成「Redis 挂了」
            logger.error("submit_one_candidate Redis 操作失败: %s", e)
            return -1,False

    async def query_candidates_by_user(self,user_id: int) -> dict:
        """
        根据用户查询候选人池，解析 value 失败不崩所有的 value
        :return:
        """
        query_key: str = ProfileCandidate.key(user_id)
        ans = {}
        try:
            query: dict = await self._client.hgetall(query_key)
            # 现在需要解析 query， json.loads() 去解析 value， 如果解析不出来，那么就直接抛弃
            for field, value in query.items():
                try:
                    decoded_value = json.loads(value)
                except Exception as e:
                    # 捕捉所有的错误，防止单次序列化失败导致系统整体失效
                    logger.error("query_candidates_by_user 解析 %s 失败: %s", value, e)
                    continue
                ans[field] = decoded_value
        except RedisError as e:
            logger.error("query_candidates_by_user Redis不可用: %s", e)

        return ans

    async def clear_candidates_by_user(self,user_id):
        """清空用户的所有候选池"""
        # 这个是最底层的函数， 所以不应该去判断user_id是否合理，判断操作应该是上层的事情
        # 先根据 user_id 构造 key
        delete_key = ProfileCandidate.key(user_id)
        ans = await self._client.delete(delete_key)
        if ans == 0:
            logger.warning("clear_candidates_by_user 删除 id = %s 的候选人池失败",user_id)
            return
        logger.info("clear_candidates_by_user 删除user_id = %s 的候选人池成功", user_id)

    async def delete_field_by_given(self,user_id: int, to_delete: list[tuple[str,str]]):
        """
        晋升的偏好落库成功之后，清掉对应的候选。所有候选都在同一个 hash 里，
        一条 HDEL 带多个 field 一次删完。

        删除失败只记日志不抛出：后果是这些偏好下次再晋升一次，重复写入由落库的
        幂等合并消化掉。抛出去的话，调用方会把「已经落库成功」误报成「保存失败」。

        已知的洞：从晋升到这里删除之间，别的请求可能已经把同一个子键改成了新值、
        计数重置为 1，这里会连新值的计数一起删掉。彻底的解法是条件删除——带上晋升时的值，
        只有存着的值仍然相同才删（一个很短的 CAS 脚本），留到 Step 2 写 Lua 时一起做。
        """
        # HDEL 至少要一个 field，空调用 Redis 会报参数个数错误
        if not to_delete:
            return

        delete_key = ProfileCandidate.key(user_id)
        fields = [ProfileCandidate.generate_field_key(field, sub_key)
                  for field, sub_key in to_delete]
        try:
            deleted = await self._client.hdel(delete_key, *fields)
        except RedisError as e:
            logger.error("delete_field_by_given 删除用户 %s 的候选失败：%s", user_id, e)
            return

        if deleted < len(fields):
            # 少删了说明有候选在晋升之后、删除之前就没了：TTL 到期，或者被别的请求删掉
            logger.warning(
                "delete_field_by_given 用户 %s 应删 %s 个候选，实际删了 %s 个：%s",
                user_id, len(fields), deleted, fields,
            )

    @staticmethod
    def key(user_id: int):
        return PRE_KEY + str(user_id)

    @staticmethod
    def generate_field_key(field: str, sub_key: str) -> str:
        return f"{field}.{sub_key}"
