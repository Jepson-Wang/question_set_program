"""
用于记录某个用户画像的偏好被统计几次，如果超过阈值，那么就加入到库中

值语义（B）：同一个子键下值变了，计数归 1、存新值，而不是继续累加。
「简单点」和「难一点」是两个主张，不能凑成一次确认。B 依赖 Task 10 的值域枚举：
进得了候选池的值必然在枚举内，字符串相等就是语义相等；如果值是自由文本，
「简单点」「简单一些」会被判成不同的值，计数一路重置，稳定偏好永远晋升不了，而且不报错。

并发：读-改-写放进一个 Lua 脚本，由 Redis 串行执行。没选 WATCH，因为它的监视粒度是整个 key，
同一用户下两个无关子键的并发提交会互相触发重试，重试耗尽后返回什么还得另定语义，
而且「EXEC 真的失败并重试」这条路径很难确定性复现。也没选分布式锁：TTL 给不出正确值，
安全解锁本身又要一个 Lua CAS，等于绕不开 Lua。

Lua 的原子只是隔离性，不是回滚：脚本跑到一半出错，前面已执行的命令撤不回来。
所以脚本一律「先读、中间算、最后只写一条」，任何一步出错都不会留下半成品。
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

load_env()

logger = get_logger(__name__)

PRE_KEY = "profile:candidates:"
DEFAULT_FIELD_TTL = 604800
DEFAULT_TIMES_TO_SUBMIT = 2

# 注意：脚本里用了 HSETEX，这把候选池锁死在 Redis 8.0+（启动时由 ensure_redis_version 检查）。
# 脚本要短：超过 busy-reply-threshold（默认 5s）后 Redis 对其他客户端回 BUSY 却不会中断脚本，
# 而写过数据的脚本 SCRIPT KILL 会被拒绝，只剩 SHUTDOWN NOSAVE。写放在最后，
# 写之前的那段时间脚本都还能被杀掉。
#
# KEYS[1] = 候选池 key
# ARGV    = field 路径, 新值, 阈值, field TTL 秒数（ARGV 全是字符串，运算前要 tonumber）
# 返回    = {计数, 是否晋升(1/0)}。不能返回 Lua 的 false/nil：false 到客户端是 nil，
#           table 里的 nil 还会把后面的元素一起截断
_SUBMIT_LUA = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
local times = 1
if raw then
  -- 缺失的 field 在 Lua 里是 false 不是 nil，所以用真值判断。
  -- 解码必须 pcall：一条脏数据抛出来会让这个用户的候选池永久写不进去
  local ok, rec = pcall(cjson.decode, raw)
  if ok and type(rec) == 'table' and type(rec.value) == 'string'
     and type(rec.times) == 'number' and rec.value == ARGV[2] then
    times = rec.times + 1
  end
end
local promoted = 0
if times >= tonumber(ARGV[3]) then promoted = 1 end
local record = cjson.encode({value = ARGV[2], times = times})
if raw then
  redis.call('HSETEX', KEYS[1], 'KEEPTTL', 'FIELDS', 1, ARGV[1], record)
else
  redis.call('HSETEX', KEYS[1], 'EX', tonumber(ARGV[4]), 'FIELDS', 1, ARGV[1], record)
end
return {times, promoted}
"""

# 条件删除：只删存着的值仍等于晋升时的值的 field。
# ARGV = field1, 晋升值1, field2, 晋升值2, ...
# 同样先读完、最后只有一条 HDEL；HDEL 不带 field 会报参数个数错误，所以没有命中就不发
_CONFIRM_DELETE_LUA = """
local matched = {}
for i = 1, #ARGV, 2 do
  local raw = redis.call('HGET', KEYS[1], ARGV[i])
  if raw then
    local ok, rec = pcall(cjson.decode, raw)
    if ok and type(rec) == 'table' and rec.value == ARGV[i + 1] then
      table.insert(matched, ARGV[i])
    end
  end
end
if #matched == 0 then return 0 end
return redis.call('HDEL', KEYS[1], unpack(matched))
"""


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
        self._submit_script = None
        self._confirm_delete_script = None

    def _submit(self):
        if self._submit_script is None:
            self._submit_script = self._client.register_script(_SUBMIT_LUA)
        return self._submit_script

    def _confirm_delete(self):
        if self._confirm_delete_script is None:
            self._confirm_delete_script = self._client.register_script(_CONFIRM_DELETE_LUA)
        return self._confirm_delete_script

    async def submit_one_candidate(self,user_id: int,field:str, sub_key: str, value: str) -> tuple[int,bool]:
        """
        提交一个候选人到 redis 库中, 如果 field 达到落库要求了，就返回 true
        field只接受str,如果为list[str]，请循环调用

        计数规则见模块注释（B 语义）。晋升不删候选：删除是落库成功之后
        delete_field_by_given 的事。Redis 不可用时返回 (-1, False)。
        """
        key = ProfileCandidate.key(user_id)
        field_path = ProfileCandidate.generate_field_key(field, sub_key)
        try:
            times, promoted = await self._submit()(
                keys=[key],
                args=[field_path, value, self.times_to_submit, self.field_ttl],
            )
        except RedisError as e:
            # 只降级 Redis 故障：候选池写不进去不该拖垮整个请求。Lua 运行期错误是
            # ResponseError，也是 RedisError，走这里；AttributeError 之类的编程错误不在这里捕获，
            # 让它抛出去，否则 bug 会被伪装成「Redis 挂了」
            logger.error("submit_one_candidate Redis 操作失败: %s", e, exc_info=True)
            return -1, False
        return int(times), bool(promoted)

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

    async def delete_field_by_given(self, user_id: int, to_delete: list[tuple[str, str, str]]):
        """
        晋升的偏好落库成功之后，清掉对应的候选。to_delete 的每项是
        (field, sub_key, 晋升时的值)，一个脚本一次处理完。

        条件删除（CAS）：从晋升到这里之间，别的请求可能已经把同一个子键改成了新值、
        计数重置为 1。盲删会把这个新主张的计数一起抹掉，所以只删存着的值
        仍等于晋升值的 field，对不上的留着，它自己继续累计。

        删除失败只记日志不抛出：后果是这些偏好下次再晋升一次，重复写入由落库的
        幂等合并消化。抛出去的话，调用方会把「已经落库成功」误报成「保存失败」。
        """
        # 脚本里 HDEL 至少要一个 field，所以空列表在这里就返回
        if not to_delete:
            return

        key = ProfileCandidate.key(user_id)
        args = []
        for field, sub_key, value in to_delete:
            args.extend([ProfileCandidate.generate_field_key(field, sub_key), value])
        try:
            deleted = await self._confirm_delete()(keys=[key], args=args)
        except RedisError as e:
            logger.error("delete_field_by_given 删除用户 %s 的候选失败：%s", user_id, e)
            return

        if deleted < len(to_delete):
            # 少删了说明有候选在晋升之后、删除之前就不是当时的样子了：
            # TTL 到期，或者被别的请求改成了不同的值（后者就是并发真的发生过）
            logger.warning(
                "delete_field_by_given 用户 %s 应删 %s 个候选，实际删了 %s 个，"
                "其余已过期或被并发改成了不同的值：%s",
                user_id, len(to_delete), deleted, [args[i] for i in range(0, len(args), 2)],
            )

    @staticmethod
    def key(user_id: int):
        return PRE_KEY + str(user_id)

    @staticmethod
    def generate_field_key(field: str, sub_key: str) -> str:
        return f"{field}.{sub_key}"
