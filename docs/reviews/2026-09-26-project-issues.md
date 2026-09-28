# 项目问题清单

> 记录时间：2026-09-26
> 对应代码：`backend` 分支 `cb47dda`（外加尚未提交的 Task 9 半成品）
> 用途：逐条修复的待办底稿。修完一条就在标题后打勾，并记下实际怎么改的。

**更新记录**

- 2026-09-26：第 15 条本机部分已解决（Redis 升到 8.10.2），生产环境版本仍待确认；升级过程中发现的内存淘汰策略问题新增为第 16 条。

## 怎么读这份清单

按「坏掉时会不会有人发现」排序，而不是按修起来的难易。

排在最前面的几条有一个共同特征：**它们本身是保护机制，而它们失效时不会报错**。CI 依然全绿，测试依然通过，唯一的信号是某天线上出了事，回头才发现某个检查从来没生效过。

这类问题比「跑起来就崩」的严重得多 —— 后者会立刻逼你修，前者会安静地躺几个月。

---

# 🔴 P0：静默失效

## 1. `REQUIRE_REDIS` 用精确字符串比较，配错值会退回静默跳过

**位置**：`backend/tests/conftest.py:56`

```python
if os.getenv("REQUIRE_REDIS") == "1":
```

**失败场景**：CI 里把 `REQUIRE_REDIS: "1"` 改成 `REQUIRE_REDIS: true`。YAML 的 `true` 传到 Actions 里是字符串 `"true"`，`== "1"` 不成立，于是走 `pytest.skip` —— **所有 Redis 测试一条不跑，CI 全绿**。

这恰好就是这个机制被创造出来要防的事。

**为什么容易发生**：同一个仓库里 `backend/core/app_settings.py:14` 的布尔判断是宽松的（接受 `1/true/yes/on`）。两套写法并存，改配置的人没理由猜到这里是精确匹配。

**修的方向**：和 `debug_enabled()` 共用同一套布尔解析。顺带补一条测试覆盖 `"true"` 这种取值 —— 现在的 `test_redis_policy.py` 只测了 `"1"` 和「未设置」两种。

---

## 2. `isolation.CONFIG_KEYS` 已经漏了 14 个环境变量

**位置**：`backend/tests/isolation.py:23`

**现状**：扫描代码里所有 `os.getenv` 调用与 `CONFIG_KEYS` 对比，漏掉的有：

```
COMMON_MODEL, DIGEST_MODEL, EXTRACT_MODEL, QUESTION_SET_MODEL,
JUDGE_API_KEY, JUDGE_API_URL, JUDGE_MODEL,
RERANK_API_KEY, RERANK_API_URL, RERANK_MODEL,
RAG_DB_DIR, RAG_EVAL_DIR, RAG_UPLOAD_DIR, RAG_JUDGE_PASS_SCORE
```

**失败场景**：`run_isolated` 启动子进程前会把 `CONFIG_KEYS` 里的变量从环境中剔除，因为 `load_dotenv` 不覆盖已存在的变量 —— 不剔除的话，临时 `.env` 里写的值根本进不去。漏掉的这 14 个会**继承开发机或 CI 的值**，测试结果取决于谁在哪台机器上跑。

不报错，只会让某个断言莫名其妙地挂，或者更糟 —— 莫名其妙地过。

**为什么会一直烂下去**：手工维护的清单，新增配置项时没有任何东西提醒你回来更新。上次检查漏 4 个，现在漏 14 个。

**修的方向**：光补齐这 14 个没用，下个月还会漏。加一条测试，扫描 `backend/` 下所有 `os.getenv` 的第一个参数，断言它们都在 `CONFIG_KEYS` 里 —— 让清单腐烂这件事变成一次 CI 失败。

---

## 3. CI 里没有 MySQL，asyncmy 从未连过真库

**位置**：`.github/workflows/ci.yml` 的 `test` job

**现状**：起了 Redis service 容器，`REQUIRE_REDIS=1` 连不上就失败 —— 这部分很严谨。但 `SQL_DATABASE_URL` 是占位值，指向一个不存在的库；`tests/dao/test_memory_mapper.py:17` 和 `test_profile_mapper.py` 用的都是 `sqlite+aiosqlite`。

**没被覆盖到的东西**：

- asyncmy 驱动本身（从 0.2.12 升到 0.2.14 这次改动，CI 覆盖为零）
- utf8mb4 下的字符截断
- `TEXT` 字段的长度上限
- sha1 唯一索引在 MySQL 与 SQLite 上的行为差异
- 事务隔离级别

**同一个 job 里两套标准**：对 Redis 是「连不上就红」，对 MySQL 是「不测」。

**修的方向**：给 test job 加一个 MySQL service 容器，让 mapper 测试在真库上也跑一遍。可以保留 SQLite 版本跑得快的优势，用参数化或者单独的 job 跑 MySQL 版本。

---

# 🟡 P1：安全与供应链

## 4. CORS 默认通配符，与模块自己声称的原则矛盾

**位置**：`backend/core/app_settings.py:19`

```python
raw = os.getenv("CORS_ALLOW_ORIGINS", "*")
```

同一个文件的模块注释写着：

> 默认值按「生产安全」来取：忘了配置时，宁可少开功能，也不暴露内部信息。

`APP_DEBUG` 默认关（符合），`CORS_ALLOW_ORIGINS` 默认 `*`（不符合）。docstring 里也承认了是「方便本地前端调试」。

**实际风险有多大**：比看上去小。JWT 走 `Authorization` 头而不是 cookie，通配时 `allow_credentials` 是 `False`，浏览器不会自动带上用户凭据，第三方页面拿不到用户身份。

**但是**：任何网站的 JS 都能读到 `/health/ready` 的响应，里面写着哪些依赖挂了。而且一旦将来改用 cookie 认证，这一行就从「不痛不痒」变成真的洞。

**修的方向**：两种都行，选一种并让注释和代码一致 —— 要么默认收紧（本地开发显式配 `*`），要么保留默认但把模块注释改成实话。**不要留着一句自己都不遵守的原则。**

---

## 5. 装了两个检查工具，一次都没跑

**位置**：`backend/requirements-tools.txt` 与 `.github/workflows/ci.yml`

| 工具 | 在 ci.yml 里出现 |
|---|---|
| `ruff` | 2 次 ✅ |
| `hadolint-py` | 2 次 ✅ |
| `shellcheck-py` | **0 次** |
| `pip-audit` | **0 次** |

lint job 每次都 `pip install -r backend/requirements-tools.txt` 把它们装上，然后什么也不做。

**为什么比不装更糟**：`pip-audit` 是供应链漏洞扫描器。看依赖清单的人（包括半年后的你）会以为项目有依赖扫描。

**修的方向**：要么在 CI 里真的调用，要么从清单里删掉。`shellcheck` 可以等到 Task 9 写部署脚本时再加 —— 那时候才有 `.sh` 文件可扫。

---

## 6. 对自己下载的二进制校验 sha256，对别人的 action 只钉 tag

**位置**：`.github/workflows/ci.yml`

下载 actionlint 时：

```yaml
ACTIONLINT_SHA256: 8aca8db9...
echo "${ACTIONLINT_SHA256}  actionlint.tar.gz" | sha256sum -c -
```

但同一个文件里所有第三方 action 都是可变引用：

```
actions/checkout@v7            (L33, L89, L135, L154)
actions/setup-python@v7        (L35, L91)
actions/upload-artifact@v7     (L118)
actions/setup-node@v7          (L137)
docker/setup-buildx-action@v4  (L156)
docker/build-push-action@v7    (L159)
```

**tag 是可变的。** 拿到 action 仓库写权限的人可以把 `v7` 指向任意 commit，而这些 action 拿到的权限比 actionlint 高得多。同一个攻击面，两套标准。

**修的方向**：要么把 action 钉到 commit SHA（配 Dependabot 自动更新），要么承认 tag pin 是可接受的风险，把 actionlint 那段 sha256 校验的注释改得别那么理直气壮。**一致比严格更重要。**

---

# 🟡 P2：质量门槛

## 7. 覆盖率收集了，但没有下限

**位置**：`.github/workflows/ci.yml` / `backend/.coveragerc` / `backend/pytest.ini`

当前 `TOTAL 69%`，写进了 GitHub 的运行摘要，但**没有 `--cov-fail-under`**。覆盖率可以一路掉到 5% 而 CI 一声不吭。

**顺带一提这个 69% 是低估的**：`isolation.py` 起的子进程里跑的代码不计入统计 —— `security.py` 的 fail-fast 分支、`main.py` 的整个启动路径，实际测了但显示为未覆盖。想要准确数字得用 `coverage` 的 `--parallel-mode` 加 `concurrency=multiprocessing`。

**修的方向**：先决定这个数字准不准要不要管。不管的话，按当前值减几个点设一个下限（比如 65%），防止大幅倒退；要准的话，先配好子进程覆盖率收集，再设线。

---

## 8. 生产镜像里装了 12 个测试和构建工具

**位置**：`backend/requirements.txt`

```
build, coverage, griffe, griffecli, griffelib,
iniconfig, pluggy, pyproject_hooks,
pytest, pytest-asyncio, pytest-cov, sympy
```

同时 `.dockerignore:12` 又把 `backend/tests` 排除在镜像之外。**一边不装测试代码，一边装测试框架**，两个决定互相矛盾。

其中 `sympy` 全项目**零引用** —— 唯一的 `from sympy import Lambda` 在 `ee089f4` 里删掉了，它是被那行误引入带进来的。

**修的方向**：把 requirements 拆成运行时和开发两份，Dockerfile 只装前者。拆的时候注意 `requirements.txt` 现在是完整锁定的传递依赖清单，不能手工挑 —— 用 `pip-compile` 之类的工具从顶层依赖重新生成。

---

## 9. 为还没实现的 RAG 扛着几百 MB

**位置**：`backend/requirements.txt`

全仓库 `import llama_index` 的只有 `backend/agents/agent/embedding.py`，而它只服务 RAG；`chromadb` 是**零 import**。但 `chromadb`、`onnxruntime`（它的依赖，单独 200MB+）、`llama-index-*` 全在清单里，都会进镜像。

RAG 计划（`docs/superpowers/plans/2026-09-11-rag-knowledge-base.md`）还没开始做。

**修的方向**：和第 8 条一起处理，做成可选依赖（extras），等 RAG 真正落地再装进镜像。镜像体积会直接影响部署速度和 CI 构建时间。

---

# 🟢 P3：一致性与陈旧信息

## 10. 部署计划里的 asyncmy 版本是旧的

- `docs/superpowers/plans/2026-09-15-github-actions-cicd.md:1249` 写 `0.2.12`
- `docs/superpowers/plans/2026-09-15-production-deployment.md:19` 写 `0.2.12`
- `backend/requirements.txt:8` 实际是 `0.2.14`

**修的方向**：改文档。顺手检查这两份计划里还有没有别的已经过时的断言。

---

## 11. `ShortTermMemory` 的 docstring 描述了一个不存在的参数

**位置**：`backend/agents/memory/short_term_memory.py:61-75`

```python
def __init__(self, max_memory_size=10, ttl=DEFAULT_TTL, pending_ttl=DEFAULT_PENDING_TTL):
    """
    Args:
        redis_client: Redis客户端实例，可选，若不传则自动获取全局实例    ← 文档里有
    """
    self._client = get_redis_client().client                          ← 签名里没有
```

**这不只是文档错误。** 因为没有这个参数，`tests/agents/memory/test_short_term_memory.py` 只能用 `monkeypatch.setattr(memory, "_client", ...)` 事后替换 —— 测试被迫依赖一个私有属性的名字，改名就会静默失效。

对比 `MemoryMapper` / `UserProfileMapper`：它们的构造函数收 `session_factory`，测试直接传一个 SQLite 工厂进去，一行搞定，不碰任何内部字段。

**修的方向**：给 `__init__` 加上 `client` 参数（docstring 早就这么承诺了），测试 fixture 改成构造时注入。同样的问题在新写的 `ProfileCandidate` 里也存在，一起改。

---

## 12. `test_redis_policy.py` 从 conftest 里 import

**位置**：`backend/tests/test_redis_policy.py:3`

```python
from backend.tests.conftest import redis_unavailable
```

能跑是因为 `backend/tests/__init__.py` 存在，pytest 把 conftest 当包内模块导入，和这里 import 到的是同一个对象。去掉那个 `__init__.py` 就会出现两份 conftest 模块、两次 `load_env()`、两套模块级状态。

更根本的是：`conftest.py` 是 pytest 的配置入口，不是共享工具库。项目里已经有正确的地方了 —— `backend/tests/isolation.py`。

**修的方向**：把 `redis_unavailable` 挪到 `isolation.py`，conftest 从那里 import。

---

## 13. `UserProfileUpdateRequest` 带着 `create_time`，更新时会覆盖创建时间

**位置**：`backend/schemas/request/user_profile_update_request.py:16-17`

`update_user_profile` 的赋值循环对不在合并/追加名单里的字段一律 `setattr`。`create_time` 正好落在 else 分支，所以只要有人传了它，画像的创建时间就会被改掉。

**当前没有调用方传它**（`long_term_memory.py` 只传 `update_time`），所以这是潜在问题而非现存 bug。

**修的方向**：要么从这个 schema 里删掉 `create_time`（更新请求本来就不该带创建时间），要么在赋值循环里显式排除。

---

## 14. 记忆系统的端到端验证从来没跑过

**现状**：所有摘要相关的测试用的都是 `FakeLLM`，所有 mapper 测试跑在 SQLite 上。`backend/agents/skills/session_digest/SKILL.md` 这个提示词**至今没有见过真实模型的输出**。

**为什么要在意**：单测能证明「代码按设计运行」，证明不了「设计出来的摘要有用」。摘要质量差、丢关键信息、或者模型不按格式返回，这些只有真跑才知道。

**怎么做**：用同一个 `user_id` / `session_id` 连续对话 12 轮以上，触发至少两次摘要重建，然后直接去库里看：

- `memory_record` 的行数和内容对不对
- **`memory_digest.summary` 实际写了什么** —— 这条最重要，要人去读

---

# 环境问题（不在代码里，但会挡住开发）

## 15. Redis 版本与 hash field 级 TTL　✅ 本机已解决 / ⚠️ 生产待确认

### 原问题

本机 Redis 是 **7.0.15**，而 `HEXPIRE` / `HPTTL` / `HPERSIST` 是 **Redis 7.4** 才加的：

```
HEXPIRE -> ResponseError: unknown command 'HEXPIRE'
HPTTL   -> ResponseError: unknown command 'HPTTL'
```

Task 9 的候选池打算用 field 级 TTL（比 key 级 TTL 语义更准确 —— 活跃用户的老候选不会被新提交无限续命），在 7.0 上跑不起来。

### 本机已解决（2026-09-26）

升到 **Redis 8.10.2**。Redis 跑在 WSL Ubuntu 24.04 里，通过 `wslrelay.exe` 转发到 Windows 的 6379。

实测确认是真正的 field 粒度：

```
HEXPIRE a 100  -> [1]        成功
HPTTL   a      -> [99999]    该 field 剩 ~100 秒
HPTTL   b      -> [-1]       同一个 hash 里的另一个 field 没有过期时间
整个 key 的 TTL -> -1         key 本身不过期
```

升级后全套测试 128 passed，无回归。

**过程中踩的两个坑，记下来备查**：

1. `sudo apt-get update && sudo apt-get install ... redis` —— `update` 因为无关的 rabbitmq 源缺 GPG key 而返回非零，`&&` 短路，**install 从来没执行**。表现是「命令跑完了但版本没变」，很容易误判成安装失败。
2. `--force-confold` 保住了 `redis.conf` 里的 `requirepass`，但也保住了 `daemonize yes`；Redis 官方包的 systemd unit 是 `Type=notify`，等不到 READY 通知，于是把已经起来的进程 SIGTERM 掉，报 `Result: protocol`。改成 `daemonize no` + `supervised systemd` 后正常。

同时清掉了 WSL 里那个坏掉的 mysql-server-8.0（`Error: 22`，WSL 上的已知问题；应用实际用的是 Windows 的 MySQL80 服务，这个是没用的残留），dpkg 现在 0 个损坏包。

### 仍未确认：生产环境

**自建 Redis** 版本随你选；**云托管**（阿里云、腾讯云等）的可用版本由厂商定，目前普遍提供到 7.x，**7.4 以下没有 `HEXPIRE`**。

如果生产上不去 7.4，field 级 TTL 的代码到线上就是 `unknown command`，只能退回这两个备选：

| 备选 | 取舍 |
|---|---|
| key 级 TTL | 兼容所有版本；活跃用户的老候选会被新提交不断续命 |
| 过期时间写进 value，读时自己判断 | 完全不依赖版本；过期项不会自动消失，要靠读时过滤 + key 级 TTL 兜底 |

**部署计划 Task 1（服务器体检）时必须确认这一条。**

另外 Redis 8.x 是 **AGPLv3 / RSALv2 / SSPLv1 三重许可**（7.0 是 BSD）。自己部署不受影响，商业化场景需要留意。

---

## 16. 生产 Redis 的内存淘汰策略是默认值，内存满时会拒绝写入

**发现于**：升级后读运行时配置。

```
maxmemory-policy = noeviction
maxmemory        = 0（无上限）
```

开发机无所谓（内存管够），但**这个组合在生产上是个雷**：内存打满时 Redis 不淘汰任何键，而是让所有写入直接失败。

很多人默认「Redis 内存满了会自动删旧的」——**默认行为恰好相反**。

**影响到哪**：短期记忆（`stm:{user_id}:{session_id}`，24h TTL）和候选池（7 天 TTL）都是缓存性质的数据，内存吃紧时应该被淘汰，而不是把整个写入路径打死。

**修的方向**：生产配置里设一个 `maxmemory`，并选一个淘汰策略。两个候选：

- `allkeys-lru` —— 所有键都可能被淘汰，最省心
- `volatile-lru` —— 只淘汰设了过期时间的键。**注意陷阱**：没设 TTL 的键永远不会被淘汰，如果这类键占满内存，行为会退化成 `noeviction`

这个项目所有 Redis 键都带 TTL，两种都可以；但 `allkeys-lru` 对「将来有人写了个不带 TTL 的键」更宽容。

放进部署计划 Task 1 一起定。

---

# 建议的处理顺序

1. ~~**第 15 条**~~ —— 本机已解决，Task 9 不再被卡住；生产环境那半留到部署阶段
2. **第 1、2 条** —— 保护机制自己坏了，都在你写的测试基建里，修起来也快
3. **第 11 条** —— 顺手把 `ProfileCandidate` 和 `ShortTermMemory` 一起改成构造注入
4. **第 3 条**（CI 加 MySQL）—— 工作量较大，但它是目前最大的覆盖盲区
5. 其余按 P1 → P2 → P3 清

**留到部署计划 Task 1（服务器体检）一起定的**：第 15 条的生产 Redis 版本、第 16 条的内存淘汰策略。两条都是「本机看不出问题，上线才炸」的类型。
