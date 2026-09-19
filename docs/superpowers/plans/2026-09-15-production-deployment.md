# 生产部署实施计划（阿里云 ECS · Docker Compose · IP 直连 HTTPS）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把学生学情分析系统部署到一台 2GB 内存的阿里云 ECS（中国内地、x86_64、Ubuntu 26.04、只装了 Docker）上。通过公网 IP 用 HTTPS 访问，证书浏览器信任、自动续期；此后合并到 main 就自动部署，失败自动回滚；数据库每天自动备份。

**Architecture:** 所有服务都跑在这一台服务器的 Docker Compose 里：Nginx 负责 HTTPS 和反向代理，后面是应用，再后面是 MySQL 和 Redis。对外只开放 80 和 443 端口。HTTPS 证书用 Let's Encrypt 的 IP 地址证书，由 Certbot 通过 HTTP-01 验证申请，有效期约 6 天，systemd 定时器负责自动续期。应用镜像由 GitHub Actions 构建，推送到阿里云容器镜像服务（ACR），服务器从同地域的 VPC 内网地址拉取。

**Tech Stack:** Docker Compose / Nginx 1.30 / Certbot 5.8.0（Let's Encrypt `shortlived` 配置）/ MySQL 8.4 / Redis 7 / 阿里云 ACR 个人版 / systemd timer / GitHub Actions

**Spec:** 没有独立的设计文档，需求见下面的「部署要求」。

## 和 CI/CD 计划的关系（必读）

本计划接在 `docs/superpowers/plans/2026-09-15-github-actions-cicd.md`（下面简称「CI/CD 计划」）后面：

| CI/CD 计划的任务 | 在本计划中的处理 |
|---|---|
| Task 1–8（测试隔离、Redis 策略、JWT 密钥、依赖、ruff、健康检查、CI、Dockerfile） | **前置条件，必须先完成**。本计划直接使用它们的成果：`/health/ready`、`JWT_SECRET_KEY`、`asyncmy 0.2.12`、`Dockerfile`、`ci.yml` |
| Task 9（部署脚本） | **由本计划 Task 3 取代**。原版只编排了应用一个服务，现在要把 MySQL、Redis、Nginx 一起编排 |
| Task 10（漏洞扫描与 Dependabot） | 不受影响，按需完成 |
| Task 11（服务器与 GitHub 配置） | **由本计划 Task 1、4、8 取代** |
| Task 12（发布流水线） | **由本计划 Task 4、8 取代**。镜像改推阿里云 ACR，而不是 GHCR |
| Task 13（分支保护与文档） | 分支保护照做；CLAUDE.md 的内容以本计划 Task 9 的版本为准 |

**为什么改用阿里云 ACR**：中国内地的服务器访问 ghcr.io（GitHub 的镜像仓库）又慢又不稳定，经常拉到一半就断了。ACR 和服务器在同一个地域，服务器走 VPC 内网拉镜像，速度快，也不产生公网流量。

## 部署要求

| # | 要求 |
|---|---|
| D-R1 | 通过 `https://<公网IP>` 访问服务，证书被浏览器和 curl 信任，不弹安全警告 |
| D-R2 | 访问 `http://<公网IP>` 自动跳转到 HTTPS |
| D-R3 | 证书自动续期，无需人工干预 |
| D-R4 | 只对公网开放 22、80、443 三个端口；MySQL、Redis、应用端口不对外暴露 |
| D-R5 | 在 2GB 内存上稳定运行：每个服务都有内存上限，另有 swap 兜底 |
| D-R6 | 生产环境关闭调试模式，接口出错时不向调用方回显堆栈 |
| D-R7 | 合并到 main 自动部署；就绪检查失败自动回滚；可以手动回滚到任意历史版本 |
| D-R8 | MySQL 每天自动备份，保留 7 天 |
| D-R9 | 服务器重启后，所有服务自动恢复 |

## 部署架构

```
               公网（阿里云安全组只放行 22 / 80 / 443）
                        │
        ┌───────────────┴────────────────────────────── ECS（2GB，x86_64）─┐
        │  nginx:80   ── /.well-known/acme-challenge/ → certbot-www 目录     │
        │             ── 其余请求 → 301 跳转到 https                          │
        │  nginx:443  ── TLS 终止（Let's Encrypt IP 证书）→ app:8000          │
        │                                                                    │
        │  app（应用镜像，uid 10001） ── mysql:3306（仅内部网络）              │
        │   └ 127.0.0.1:8000 仅本机，给 deploy.sh 做就绪检查                  │
        │                             ── redis:6379（仅内部网络）             │
        │                                                                    │
        │  certbot（平时不运行；续期时由 systemd 定时器临时启动）               │
        └────────────────────────────────────────────────────────────────────┘
                        ▲ VPC 内网拉镜像
          阿里云 ACR（同地域） ◀── GitHub Actions 构建并推送
```

## 这份计划里哪些已经验证过

- **应用的配置开关**：`APP_DEBUG` 和 `CORS_ALLOW_ORIGINS` 的实现与测试在代码副本里跑过，新测试 11 passed；CI 模式下全量测试 41 passed（另有 20 个因本机 Redis 未启动而跳过）
- **编排文件**：`docker-compose.yml` 代入占位值之后，通过了 Compose 官方 JSON Schema 的校验
- **Nginx 配置**：用 NGINX 官方的解析器 crossplane 做了上下文和参数检查；它唯一不认识的是 `http2 on;`，这条指令在 nginx 官方文档中确认过（1.25.1 起可用，本计划用的是 1.30）
- **部署脚本**：`deploy.sh` 的 5 个桩测试用例全部通过，每道防护都做过变异验证；4 个 shell 脚本都通过了 shellcheck
- **发布流水线**：两个阶段的 `release.yml` 都通过了 actionlint（含 shellcheck）
- **证书相关**：Certbot 5.8.0 的 `--ip-address`、`--preferred-profile`、`--cert-name`、`--deploy-hook`、`--dry-run` 等参数，都在它的 `--help all` 输出中确认过；自签名 IP 证书的 openssl 命令在本机实测可用
- **外部事实**，编写时（2026-09-15）查证过：
  - Let's Encrypt 自 2026-01-15 起正式签发 IP 地址证书，有效期 160 小时，必须使用 `shortlived` 配置，只能通过 HTTP-01（80 端口）或 TLS-ALPN-01 验证
  - Certbot 从 5.3 起支持 `--ip-address`，从 5.4 起 webroot 方式也支持 IP；寿命短于 10 天的证书在剩余一半时续期，并支持 ARI
  - 由 ARI 协调的续期不受频率限制；其他情况下，同一组标识每 7 天最多签发 5 张
  - Docker Hub 上存在 `certbot/certbot:v5.8.0`、`nginx:1.30-alpine`、`mysql:8.4`、`redis:7-alpine`，都提供 amd64 镜像
  - ACR 个人版可以免费使用，每个账号限一个实例；公网地址是 `crpi-xxx.<地域>.personal.cr.aliyuncs.com`，VPC 地址是 `crpi-xxx-vpc.<地域>.personal.cr.aliyuncs.com`
- **内存估算**：在本机（Windows）导入完整应用后，工作集约 250MB。Linux 上的数值会有差别，所以 Task 5 会在服务器上实测

**没法在本机验证、第一次执行时要重点看的：**

- 本机没有 Docker，也没有服务器，所以编排文件、Nginx、MySQL 配置、Certbot 签发、systemd 定时器都是在 Task 5 到 7 第一次真正运行。每一步都写了验证方法和常见失败的排查思路
- **中国内地的 80 和 443 端口政策**：在中国内地用 80、443 端口对外提供网站服务，按规定需要备案。阿里云是按域名检查备案的，用 IP 访问一般不会被拦截，但我没法替你实测。Task 6 专门准备了这种情况下的判断方法和替代方案

## 知识点索引

| 编号 | 知识点 | 在哪里学 |
|---|---|---|
| P1 | 小内存服务器的资源规划与 swap | Task 1、5 |
| P2 | 云安全组与主机防火墙；Docker 发布的端口会绕过 UFW | Task 1 |
| P3 | SSH 加固：专用部署用户、只允许密钥登录 | Task 1 |
| P4 | 「生产安全」的默认值 | Task 2 |
| P5 | CORS：跨域来源与携带凭据 | Task 2 |
| P6 | 反向代理：TLS 终止、真实客户端 IP、超时、SSE 的缓冲 | Task 3 |
| P7 | 容器网络与 DNS：服务名访问、Nginx 定期重新解析上游地址 | Task 3 |
| P8 | Compose：健康检查与启动顺序、profiles、内存上限、日志轮转、YAML 锚点、`${VAR:?}` 必填变量 | Task 3 |
| P9 | MySQL 小内存调优 | Task 3 |
| P10 | Redis 持久化（AOF）与内存淘汰策略 | Task 3 |
| P11 | 镜像仓库：地域、公网地址与 VPC 地址 | Task 4 |
| P12 | HTTPS 证书链、ACME 协议、HTTP-01 验证、IP 证书与 SNI | Task 6 |
| P13 | 证书的「先有鸡还是先有蛋」：用自签名证书引导 | Task 5 |
| P14 | 频率限制，以及用 staging / `--dry-run` 先演练 | Task 6 |
| P15 | systemd timer 与 cron | Task 7 |
| P16 | 备份：逻辑备份、保留策略、磁盘快照、恢复演练 | Task 7、9 |
| P17 | 部署状态的记录、自动回滚与手动回滚 | Task 3、8 |

## Global Constraints

- 服务器：阿里云 ECS，中国内地，x86_64，Ubuntu 26.04，2GB 内存。镜像只构建 `linux/amd64`。
- 部署目录固定为 `/opt/question_set_program`，归 `deploy` 用户所有；应用由 `deploy` 用户运行，它只在 `docker` 组里，没有 sudo 权限。
- 对公网只开放 TCP 22、80、443。其他服务不发布端口；应用端口只绑定在 `127.0.0.1`。
- 镜像版本（2026-09-15 核对）：`mysql:8.4`、`redis:7-alpine`（与 CI 的服务容器同一个大版本）、`nginx:1.30-alpine`、`certbot/certbot:v5.8.0`。
- 内存上限：mysql 600m、redis 160m、app 800m、nginx 64m；另配 2GB swap。
- 所有密码只用十六进制随机串（`openssl rand -hex 16`），因为它们会被拼进数据库连接串。
- 证书：Let's Encrypt `shortlived`（约 6 天有效期），证书名固定为 `question-set`，通过 HTTP-01 webroot 方式验证。
- 服务器上的 `.env` 保存全部密钥，文件权限为 600，永远不进 Git、不进镜像。
- 命令示例用 bash 语法；标注「在服务器上」的命令要登录服务器执行，标注「在本机」的命令在你自己的电脑上执行（Windows 下用 Git Bash）。
- 提交信息用中文，遵循 `feat:` / `fix:` / `refactor:` / `test:` / `docs:` 前缀。

## 文件结构

**新建**

| 文件 | 职责 | 任务 |
|---|---|---|
| `backend/core/app_settings.py` | 调试模式、CORS 来源的配置读取 | 2 |
| `backend/tests/test_app_settings.py` | 上面两个开关的测试 | 2 |
| `deploy/docker-compose.yml` | 生产编排：mysql、redis、app、nginx、certbot | 3 |
| `deploy/nginx/default.conf` | HTTPS、HTTP 跳转、证书验证路径、反向代理 | 3 |
| `deploy/mysql/low-memory.cnf` | MySQL 小内存配置 | 3 |
| `deploy/.env.production.example` | 服务器 `.env` 的模板 | 3 |
| `deploy/deploy.sh` | 部署与自动回滚 | 3 |
| `deploy/test_deploy.sh` | `deploy.sh` 的桩测试 | 3 |
| `deploy/renew_cert.sh` | 证书续期 | 3 |
| `deploy/backup_mysql.sh` | 数据库备份 | 3 |
| `deploy/systemd/qsp-cert-renew.{service,timer}` | 每天两次检查续期 | 3 |
| `deploy/systemd/qsp-backup.{service,timer}` | 每天一次备份 | 3 |
| `.github/workflows/release.yml` | 构建镜像推送到 ACR，自动部署 | 4、8 |

**修改**

| 文件 | 改动 | 任务 |
|---|---|---|
| `backend/main.py` | `debug`、CORS 改为从配置读取 | 2 |
| `backend/tests/isolation.py` | `CONFIG_KEYS` 加上 `APP_DEBUG`、`CORS_ALLOW_ORIGINS` | 2 |
| `.github/workflows/ci.yml` | 静态检查加 shellcheck；后端测试加部署脚本的测试 | 3 |
| `CLAUDE.md` | 部署章节 | 9 |

---

## Task 1: 服务器体检与基础准备

**目标**：确认服务器的实际情况和假设一致，把后面每一步都要用到的基础设施准备好。这一步全部是手动操作，每一小步都有检查方法，**有一项不符合就先停下来**。

**Files:** 无（只在服务器和阿里云控制台上操作）

- [ ] **Step 1: 看清服务器的底细**

**知识点**：P1 资源规划

在服务器上：

```bash
uname -m                              # 期望 x86_64
. /etc/os-release && echo "$PRETTY_NAME"
free -h                               # 内存约 2G；Swap 一行现在多半是 0
df -h /                               # 根分区剩余空间至少 15G：镜像约 2G，加上数据、日志和备份
nproc
docker version --format '{{.Server.Version}}'
docker compose version                # 必须能打印版本号
```

如果 `docker compose version` 报 `docker: 'compose' is not a docker command`，说明缺 compose 插件：
- 用 Ubuntu 自带仓库装的 Docker（`docker.io` 包）：`sudo apt install -y docker-compose-v2`
- 用 Docker 官方仓库装的：`sudo apt install -y docker-compose-plugin`

装完再执行一次 `docker compose version`。

- [ ] **Step 2: 确认能拉到要用的镜像**

```bash
for image in mysql:8.4 redis:7-alpine nginx:1.30-alpine certbot/certbot:v5.8.0; do
  echo "== $image"; sudo docker pull "$image" | tail -1
done
```

Expected: 每个镜像最后都输出一行 `Status: Downloaded newer image for ...`（或者 `Image is up to date`）。

`hello-world` 能拉到，不代表大镜像也能顺利拉完。如果某个镜像超时：打开阿里云控制台 → 容器镜像服务 → 镜像工具 → 镜像加速器，复制「加速器地址」，然后执行：

```bash
sudo mkdir -p /etc/docker
echo '{"registry-mirrors": ["你的加速器地址"]}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```

重启完再执行一遍上面的拉取命令。如果 `/etc/docker/daemon.json` 原本就有内容，要手动把 `registry-mirrors` 合并进去，不要整个覆盖掉。

- [ ] **Step 3: 加 2GB swap**

**知识点**：P1 资源规划

在服务器上：

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf
sudo sysctl --system >/dev/null
free -h
```

Expected: `Swap:` 一行显示 2.0Gi。

- **为什么要 swap**：2GB 内存上要跑 MySQL、应用、Redis、Nginx，外加系统和 Docker，平时够用，但峰值时比较紧，比如应用启动时加载模型库，或者 MySQL 做备份。没有 swap 时，内存一耗尽，内核的 OOM killer 会直接杀掉占内存最多的进程，通常就是 MySQL 或应用。有了 swap，峰值时只是变慢，不会崩溃
- **`swappiness=10`**：告诉内核尽量用物理内存，实在不够了才用 swap。默认值 60 会过早地把内存页换出去，拖慢速度
- **写进 `/etc/fstab`**：服务器重启后自动启用 swap

- [ ] **Step 4: 阿里云安全组**

**知识点**：P2 安全组与防火墙

阿里云控制台 → 云服务器 ECS → 实例 → 你的实例 → 安全组 → 配置规则 → 入方向，确保只有这三条「允许」规则（来源都是 `0.0.0.0/0`）：

| 端口 | 用途 |
|---|---|
| TCP 22 | SSH。GitHub Actions 的 IP 不固定，所以来源只能是全部；安全性靠下一步的「只允许密钥登录」来保证 |
| TCP 80 | Let's Encrypt 验证，以及跳转到 HTTPS |
| TCP 443 | HTTPS |

其他入方向的允许规则全部删掉，特别是 3306、6379、8000 这些端口，如果有的话。

**安全组和主机防火墙（ufw）不是一回事。** 安全组在云平台的网络层面过滤，流量根本到不了你的服务器；ufw 在服务器内部过滤。本计划只用安全组。**不建议在这台机器上依赖 ufw**：Docker 发布端口时会直接修改 iptables 规则，这些规则的优先级比 ufw 高，于是会出现「ufw 显示已拒绝，端口其实照样对外开放」的情况。这也是本计划不给 MySQL、Redis 发布任何端口的原因：没有发布，就不存在被误开放的可能。

- [ ] **Step 5: 专用的部署用户**

**知识点**：P3 SSH 加固

在服务器上（用你现在的管理员账号）：

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
sudo mkdir -p /opt/question_set_program
sudo chown deploy:deploy /opt/question_set_program
sudo -iu deploy docker ps     # 能列出容器表头（空列表也行），说明 deploy 用户能使用 Docker
```

- `deploy` 用户专门用来运行服务、接受 GitHub Actions 的部署，**不给 sudo 权限**。GitHub 那边的私钥万一泄露，攻击者也拿不到 root
- 需要提醒的一点：`docker` 组的成员实际上能间接获得 root 权限，比如挂载宿主机的根目录。所以部署私钥仍然要严格保管（Task 8 会存进 GitHub 环境的 Secrets 里）

- [ ] **Step 6: 只允许密钥登录**

**知识点**：P3 SSH 加固

**先确认你自己能用密钥登录，再关掉密码登录，顺序不能反，否则会把自己锁在门外。**

1. 如果你现在是用密码登录的，先在**本机**生成一对你自己的密钥（已经有的话跳过），并把公钥装到服务器上：

   ```bash
   ssh-keygen -t ed25519 -C "你的名字"            # 一路回车，建议设一个密钥口令
   ssh-copy-id root@你的公网IP                      # 或者你平时登录用的那个用户
   ssh root@你的公网IP                              # 确认不输密码也能进来
   ```

2. 能用密钥登录之后，在服务器上关闭密码登录：

   ```bash
   printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/00-hardening.conf
   sudo sshd -t && sudo systemctl reload ssh
   sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication) '
   ```

   Expected: 最后一条输出 `passwordauthentication no` 和 `kbdinteractiveauthentication no`。

   **文件名必须以 `00-` 开头。** 云服务器的 Ubuntu 镜像通常自带 `/etc/ssh/sshd_config.d/50-cloud-init.conf`，里面写着 `PasswordAuthentication yes`。sshd 对同一个选项只采用**第一次**读到的值，而这个目录里的文件按文件名顺序读取。如果把加固配置命名为 `99-...`，它排在 `50-cloud-init.conf` 后面，会被悄悄忽略掉。`sshd -T` 打印的是 sshd 实际生效的配置，改完一定要用它确认。

3. **不要关掉当前的连接**，另开一个终端再登录一次，确认仍然能进来。

万一真的把自己锁在外面了：阿里云控制台 → 实例 → 远程连接 → VNC 远程连接，可以在网页里登录服务器改回来。

互联网上的机器每天都会被自动扫描、尝试爆破 SSH 密码。只允许密钥登录之后，这类攻击就没有用了。

---

## Task 2: 生产环境的配置开关

**目标**：`backend/main.py` 里写死了 `debug=True`，CORS 允许任意来源并允许携带凭据。本地开发时没问题，但放到公网上就不合适。把这两项改为从配置读取，并且默认值按「生产安全」来取。

**为什么 debug 必须关掉**：FastAPI 开启调试模式后，接口一旦抛出未捕获的异常，就会把**完整的错误堆栈**作为响应返回给调用方，里面有文件路径、代码片段，有时还有 SQL 语句和配置值。对攻击者来说，这就是一份免费的内部结构说明书。

**Files:**
- Create: `backend/core/app_settings.py`
- Create: `backend/tests/test_app_settings.py`
- Modify: `backend/main.py`
- Modify: `backend/tests/isolation.py`（`CONFIG_KEYS` 加两项）

**Interfaces:**
- Consumes: CI/CD 计划 Task 1 的 `run_isolated`、`write_env_file`、`CONFIG_KEYS`
- Produces:
  - `debug_enabled() -> bool`：读取 `APP_DEBUG`；不设或者设成 `false` / `0` / 空字符串时返回 False
  - `cors_allow_origins() -> list[str]`：读取 `CORS_ALLOW_ORIGINS`（逗号分隔），不设时返回 `["*"]`
  - 配置项 `APP_DEBUG`、`CORS_ALLOW_ORIGINS`

- [ ] **Step 1: 写失败的测试**

**知识点**：P4 生产安全的默认值、P5 CORS

创建 `backend/tests/test_app_settings.py`：

```python
"""生产与开发用不同的开关：调试模式默认关闭，CORS 来源可配置。"""
import pytest

from backend.core.app_settings import cors_allow_origins, debug_enabled
from backend.tests.isolation import run_isolated, write_env_file


@pytest.mark.parametrize("raw, expected", [
    (None, False), ("false", False), ("0", False), ("", False),
    ("true", True), ("1", True), ("ON", True),
])
def test_debug_flag(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("APP_DEBUG", raising=False)
    else:
        monkeypatch.setenv("APP_DEBUG", raw)
    assert debug_enabled() is expected


def test_cors_defaults_to_wildcard(monkeypatch):
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    assert cors_allow_origins() == ["*"]


def test_cors_parses_comma_separated_list(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", " https://1.2.3.4 , http://localhost:5173 ,")
    assert cors_allow_origins() == ["https://1.2.3.4", "http://localhost:5173"]


APP_ENV = {
    "SQL_DATABASE_URL": "mysql+asyncmy://u:p@127.0.0.1:3306/db",
    "API_KEY": "k",
    "API_URL": "http://127.0.0.1:9/v1",
    "JWT_SECRET_KEY": "s",
}
INSPECT_APP = (
    "import backend.main as m\n"
    "cors = next(x for x in m.app.user_middleware if x.cls.__name__ == 'CORSMiddleware')\n"
    "print(m.app.debug, cors.kwargs['allow_origins'], cors.kwargs['allow_credentials'])\n"
)


def test_app_is_production_safe_by_default(tmp_path):
    """什么都不配时：不开调试模式（出错不回显堆栈）；来源是通配符时不允许携带凭据"""
    env_file = write_env_file(tmp_path / ".env", APP_ENV)
    result = run_isolated(INSPECT_APP, env_file, cwd=tmp_path, extra_env={"ANONYMIZED_TELEMETRY": "False"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False ['*'] False"


def test_app_uses_configured_origins(tmp_path):
    env_file = write_env_file(tmp_path / ".env", {**APP_ENV, "APP_DEBUG": "true", "CORS_ALLOW_ORIGINS": "https://1.2.3.4"})
    result = run_isolated(INSPECT_APP, env_file, cwd=tmp_path, extra_env={"ANONYMIZED_TELEMETRY": "False"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True ['https://1.2.3.4'] True"
```

后两个测试用子进程导入整个应用，检查 FastAPI 实例上真正生效的配置。前面几个测试只能证明「函数返回值对」，这两个能证明「`main.py` 确实用上了这些函数」。

在 `backend/tests/isolation.py` 里，给 `CONFIG_KEYS` 末尾加上 `"APP_DEBUG", "CORS_ALLOW_ORIGINS"` 这一行，改完是这样：

```python
CONFIG_KEYS = (
    "API_KEY", "API_URL", "MODEL_NAME", "EMBEDDING_MODEL", "SQL_DATABASE_URL", "JWT_SECRET_KEY",
    "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_USERNAME", "REDIS_DB",
    "APP_DEBUG", "CORS_ALLOW_ORIGINS",
)
```

不加的话，如果你本机的 `.env` 里写了 `APP_DEBUG=true`，子进程会从环境里继承这个值，测试结果就会取决于你本机的配置。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_app_settings.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.core.app_settings'`

- [ ] **Step 3: 实现**

创建 `backend/core/app_settings.py`：

```python
"""
随运行环境变化的开关。开发和生产用不同的值，全部来自配置（backend/.env 或环境变量），
默认值按「生产安全」来取：忘了配置时，宁可少开功能，也不暴露内部信息。
"""
import os

from backend.core.config import load_env

load_env()


def debug_enabled() -> bool:
    """APP_DEBUG=true 时开启 FastAPI 调试模式：接口出错时会把完整堆栈返回给调用方，只能在本地开发时打开"""
    return os.getenv("APP_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def cors_allow_origins() -> list[str]:
    """CORS_ALLOW_ORIGINS：允许跨域访问的来源，逗号分隔；不配置时为 *，方便本地前端调试"""
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
```

修改 `backend/main.py`：

1. 在 `from backend.core.hooks import ...` 的下一行加入：

   ```python
   from backend.core.app_settings import cors_allow_origins, debug_enabled
   ```

2. `debug=True,` 改为 `debug=debug_enabled(),`

3. CORS 中间件那一段改为：

   ```python
   cors_origins = cors_allow_origins()
   app.add_middleware(
       CORSMiddleware,
       allow_origins=cors_origins,
       # CORS 规范不允许「任意来源」和「携带凭据」同时成立；只有明确列出来源时才允许携带凭据
       allow_credentials=cors_origins != ["*"],
       allow_methods=["*"],
       allow_headers=["*"],
   )
   ```

**CORS 是什么**：浏览器默认禁止 A 网站的网页脚本去调用 B 网站的接口，这叫同源策略。CORS 是服务器告诉浏览器「我允许哪些来源来调用我」的机制。「携带凭据」指的是跨域请求带上 Cookie。如果允许任意来源、又允许携带 Cookie，那么任何网站都能以已登录用户的身份调用你的接口，所以 CORS 规范禁止这两者同时成立。原来的写法 `["*"] + allow_credentials=True` 在 Starlette 里的实际效果，是把请求方的来源原样回显回去，等于绕开了这条限制。本项目用 `Authorization` 请求头传 JWT，不依赖 Cookie，所以关掉凭据不影响功能。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/ -v`
Expected: `test_app_settings.py` 11 passed；其余测试不受影响

- [ ] **Step 5: 本机开发环境保留调试模式**

默认值改成了关闭。如果你在本地调试时想看到错误堆栈，在本机的 `backend/.env` 里加一行：

```bash
APP_DEBUG=true
```

- [ ] **Step 6: 提交**

```bash
git add backend/core/app_settings.py backend/tests/test_app_settings.py backend/main.py backend/tests/isolation.py
git commit -m "fix: 调试模式与 CORS 来源改为从配置读取，默认按生产安全取值"
```

---

## Task 3: 部署文件

**目标**：写出服务器上要用的全部文件，并在本机把能测的部分都测一遍。做完这个任务，所有文件都已经在仓库里了，Task 5 只需要把它们传到服务器上。

**Files:**
- Create: `deploy/docker-compose.yml`、`deploy/nginx/default.conf`、`deploy/mysql/low-memory.cnf`、`deploy/.env.production.example`
- Create: `deploy/deploy.sh`、`deploy/test_deploy.sh`、`deploy/renew_cert.sh`、`deploy/backup_mysql.sh`
- Create: `deploy/systemd/qsp-cert-renew.service`、`qsp-cert-renew.timer`、`qsp-backup.service`、`qsp-backup.timer`
- Modify: `.github/workflows/ci.yml`（两处）

**Interfaces:**
- Consumes: CI/CD 计划 Task 6 的 `/health/ready`、Task 8 的镜像约定（uid 10001，可写目录为 `/app/vector_memory` 和 `/app/backend/logs`）
- Produces:
  - 服务器目录的约定：`/opt/question_set_program/` 下放 `docker-compose.yml`、`.env`、`deploy.sh`、`renew_cert.sh`、`backup_mysql.sh`，以及 `nginx/`、`mysql/`、`certs/`、`certbot-www/`、`letsencrypt/`、`backups/` 这几个目录
  - `.env` 里的 `IMAGE=` 由 `deploy.sh` 维护，记录当前线上的版本
  - `deploy.sh <镜像>`：镜像必须形如 `<仓库地址>/<命名空间>/<仓库>:sha-<7位十六进制>`，否则以退出码 2 退出；目录里没有 `.env` 时也以退出码 2 退出；成功时退出码为 0；就绪检查失败会自动回滚，退出码为 1
  - 可调参数（环境变量）：`READY_URL`（默认 `http://127.0.0.1:8000/health/ready`）、`HEALTH_RETRIES`（默认 60）、`HEALTH_INTERVAL`（默认 2 秒）

- [ ] **Step 1: 编排文件**

**知识点**：P7 容器网络、P8 Compose、P10 Redis

创建 `deploy/docker-compose.yml`：

```yaml
# 生产环境编排：Nginx（HTTPS）→ 应用 → MySQL、Redis，全部运行在这一台服务器上。
# 同目录的 .env 提供密钥与配置；其中的 IMAGE= 由 deploy.sh 维护，记录当前线上的应用版本。

x-logging: &logging
  driver: json-file
  options:
    max-size: "10m"   # 日志文件大小上限；不设的话会一直增长，直到把磁盘写满
    max-file: "3"

services:
  mysql:
    image: mysql:8.4
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?请在 .env 里设置 MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: question_set
      MYSQL_USER: app
      MYSQL_PASSWORD: ${MYSQL_PASSWORD:?请在 .env 里设置 MYSQL_PASSWORD}
      TZ: Asia/Shanghai
    volumes:
      - mysql_data:/var/lib/mysql
      - ./mysql/low-memory.cnf:/etc/mysql/conf.d/low-memory.cnf:ro
    healthcheck:
      # mysqladmin ping 只要服务端有响应就算成功（连「拒绝访问」也算），所以这里不需要密码
      test: ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1", "--silent"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 60s
    mem_limit: 600m
    logging: *logging

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command:
      - redis-server
      - --requirepass
      - ${REDIS_PASSWORD:?请在 .env 里设置 REDIS_PASSWORD}
      - --appendonly
      - "yes"            # 开启 AOF 持久化：容器重启后，短期记忆和待归档队列还在
      - --maxmemory
      - 100mb
      - --maxmemory-policy
      - noeviction       # 内存满了就拒绝写入并报错，而不是悄悄删掉还没归档的记忆
    environment:
      REDISCLI_AUTH: ${REDIS_PASSWORD}   # 让健康检查里的 redis-cli 自动带上密码
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    mem_limit: 160m
    logging: *logging

  app:
    image: ${IMAGE:?还没有部署过应用：请先执行 deploy.sh，它会把镜像写进 .env 的 IMAGE=}
    restart: unless-stopped
    env_file: .env
    environment:
      # 这几项由编排决定，覆盖 .env 里的同名配置：MySQL 和 Redis 是同一网络里的容器，用服务名访问
      SQL_DATABASE_URL: mysql+asyncmy://app:${MYSQL_PASSWORD}@mysql:3306/question_set
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      REDIS_PASSWORD: ${REDIS_PASSWORD}
      FORWARDED_ALLOW_IPS: "*"   # 应用端口只有 Nginx 和本机访问得到，可以信任代理传来的真实客户端 IP
      TZ: Asia/Shanghai
    ports:
      - "127.0.0.1:8000:8000"    # 只绑定本机回环地址：给 deploy.sh 做就绪检查用，外网访问不到
    volumes:
      - vector_memory:/app/vector_memory
      - app_logs:/app/backend/logs
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
    mem_limit: 800m
    logging: *logging

  nginx:
    image: nginx:1.30-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certs:/etc/nginx/certs:ro
      - ./certbot-www:/var/www/certbot:ro
    mem_limit: 64m
    logging: *logging

  certbot:
    image: certbot/certbot:v5.8.0
    profiles: ["tools"]          # 不随 docker compose up 启动；只在申请、续期证书时用 docker compose run 临时运行
    volumes:
      - ./letsencrypt:/etc/letsencrypt
      - ./certbot-www:/var/www/certbot
      - ./certs:/etc/nginx-certs
    logging: *logging

volumes:
  mysql_data:
  redis_data:
  vector_memory:
  app_logs:
```

**逐段解释**

**① 服务之间怎么找到彼此（P7）**。同一个 compose 项目里的服务，会被放进同一个 Docker 网络，互相用**服务名**当主机名访问。所以应用连数据库写的是 `mysql:3306`，连 Redis 写的是 `redis`。这个网络是 Docker 内部的，外面访问不进来，这就是 MySQL 和 Redis 不需要发布端口的原因。

**② 端口发布（P2）**。`ports` 决定哪些端口暴露到宿主机上：
- Nginx 发布 80 和 443，对外服务
- 应用只写了 `127.0.0.1:8000:8000`：只在本机回环地址上监听，服务器自己能访问（给 `deploy.sh` 做就绪检查），外网访问不到
- MySQL 和 Redis 完全不发布

**③ 健康检查与启动顺序（P8）**。`depends_on` 加上 `condition: service_healthy`，表示 MySQL 和 Redis 的健康检查都通过了，才启动应用。MySQL 第一次启动要初始化数据目录，可能要一两分钟，所以 `start_period: 60s` 给它留出时间，期间失败不计数。不这样做的话，应用会在数据库还没准备好时就去建表，然后启动失败。

**④ 变量替换与必填变量（P8）**。`${MYSQL_PASSWORD}` 在执行 compose 命令时，从同目录 `.env` 里读取的值替换进来。`${VAR:?提示}` 表示这个变量必须有值，没有就报错并显示提示。忘了配密码的话，服务起不来并告诉你原因，而不是悄悄以空密码启动。

**⑤ `environment` 覆盖 `env_file`**。应用通过 `env_file: .env` 拿到全部配置，但数据库地址、Redis 地址是编排层面决定的，写在 `environment` 里，覆盖 `.env` 里的同名项。这样服务器上的 `.env` 不用关心容器之间怎么连接。

**⑥ 内存上限（P1、P8）**。`mem_limit` 给每个容器设置内存上限。某个服务内存泄漏时，它会在自己的上限处被杀掉并自动重启，而不是把整台机器的内存耗尽、连累其他服务。四个上限加起来约 1.6GB，加上系统占用的约 300MB，正好卡在 2GB 附近，swap 负责兜底。

**⑦ 日志轮转（P8）**。Docker 默认的日志驱动会把容器输出无限期写进一个文件。`max-size: 10m` 加上 `max-file: 3`，每个容器最多保留 30MB 日志。`x-logging: &logging` 是 YAML 的**锚点**：定义一次，在各处用 `*logging` 引用，避免同样的配置写五遍。以 `x-` 开头的顶层键会被 Compose 忽略，专门用来放这种可以复用的片段。

**⑧ Redis 的两个关键选项（P10）**。`--appendonly yes` 开启 AOF 持久化：每一条写命令都会追加到磁盘文件里，容器重启后，短期记忆和待归档队列还在。`--maxmemory-policy noeviction` 表示内存满了就拒绝新的写入并报错。Redis 默认的策略之一是删掉旧数据腾地方，那样待归档的记忆会在没人知道的情况下消失，报错反而更安全。

**⑨ `profiles`（P8）**。`certbot` 只在申请和续期证书时才需要。放进 `tools` 这个 profile 之后，`docker compose up` 不会启动它；需要时用 `docker compose run --rm certbot ...` 临时运行，运行完容器自动删除。

**⑩ 以后接入 RAG 时，记得补数据卷**。现在挂了两个卷：`/app/vector_memory` 和日志。

> **`vector_memory` 这个卷现在是空的**：记忆计划改版后对话记忆存进了 MySQL，向量库只剩 RAG 要用（见记忆计划开头的设计变更）。
> 卷先留着——它是镜像里唯一已经 `chown` 给 uid 10001 的可写数据目录，RAG 上线时把 `RAG_DB_DIR` 指到它下面就能直接用，
> 不必再改 Dockerfile 的权限。也就是说，这条注意事项没有作废，只是「现在卷里没数据」。
RAG 知识库的目录是相对 `backend/` 解析的，也就是容器里的 `/app/backend/rag_db`、`/app/backend/rag_uploads`、`/app/backend/rag_eval`，这三个路径都在镜像内部，**不挂卷的话，每次部署重建容器，入库的题库和向量都会被清空**。RAG 上线时要在 `volumes` 里加上这三项（或者把 `RAG_DB_DIR` 等配置指到已经挂载的目录下）。

- [ ] **Step 2: Nginx 配置**

**知识点**：P6 反向代理、P7 DNS 重新解析、P12 HTTP-01 验证

创建 `deploy/nginx/default.conf`：

```nginx
# 80 端口只做两件事：给 Let's Encrypt 的验证请求提供文件；其余请求一律跳转到 HTTPS
server {
    listen 80 default_server;
    server_name _;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl default_server;
    http2 on;
    server_name _;
    server_tokens off;

    # 证书由 certbot 的 deploy-hook 复制到这里；第一次部署前先放一张自签名证书占位，否则 nginx 起不来
    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_session_cache   shared:SSL:1m;
    ssl_session_timeout 1h;

    client_max_body_size 10m;

    # 每次部署都会重建应用容器，它的 IP 可能变化。把地址放进变量，nginx 就会通过 Docker 内置 DNS（127.0.0.11）
    # 每 10 秒重新解析一次；直接写 proxy_pass http://app:8000 的话，只在启动时解析一次，部署后就 502
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $app_upstream http://app:8000;

    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 300s;   # 一次请求里 ReAct 可能调用好几轮大模型，默认的 60 秒不够

    # 流式接口（SSE）：关掉缓冲，事件才能一条条实时推给前端，而不是攒够一大块才发
    location /agent/analyse/stream {
        proxy_pass $app_upstream;
        proxy_buffering off;
        proxy_cache off;
    }

    location / {
        proxy_pass $app_upstream;
    }
}
```

**逐段解释**

**① 80 端口（P12）**。申请证书时，Let's Encrypt 会访问 `http://你的IP/.well-known/acme-challenge/<随机串>`。Certbot 事先把这个文件写进 `certbot-www` 目录，Nginx 负责把它提供出去，这个过程就叫 HTTP-01 验证。其余所有请求一律 301 跳转到 HTTPS。

**② TLS 终止（P6）**。浏览器和 Nginx 之间走 HTTPS，由 Nginx 负责加密和解密；Nginx 和应用之间走容器内部网络的普通 HTTP。应用本身不需要处理证书。

**③ 为什么用变量写 `proxy_pass`（P7）**。这是一个容易踩的坑。如果直接写 `proxy_pass http://app:8000;`，Nginx 只在**启动时**解析一次 `app` 对应的 IP，之后一直用这个 IP。而每次部署都会重建应用容器，新容器的 IP 可能不一样，于是 Nginx 还在往旧 IP 发请求，用户看到的就是 502。`resolver 127.0.0.11` 指向 Docker 内置的 DNS；把地址放进变量 `$app_upstream` 之后，Nginx 会按 `valid=10s` 的间隔重新解析。

**④ 转发请求头（P6）**。经过代理之后，应用看到的请求来源都是 Nginx 容器。`X-Forwarded-For` 和 `X-Real-IP` 把真实的客户端 IP 传过去；应用端配合设置 `FORWARDED_ALLOW_IPS`（见编排文件），uvicorn 才会采信这些请求头。`X-Forwarded-Proto` 告诉应用，原始请求是 HTTPS。

**⑤ 超时与流式接口（P6）**。Nginx 默认等后端 60 秒没有响应就返回 504；一次 ReAct 可能调用好几轮大模型，所以放宽到 300 秒。`/agent/analyse/stream` 是 SSE 流式接口，Nginx 默认会先把后端的响应攒进缓冲区，攒够了再发，这会让「实时推送」变成隔很久才一次性吐出来，所以要关掉 `proxy_buffering`。

**⑥ 为什么不加 HSTS**。HSTS 是一个响应头，告诉浏览器「以后只用 HTTPS 访问我」。浏览器会忽略用 IP 访问时收到的 HSTS（RFC 6797 的规定），加了也没有用。

- [ ] **Step 3: MySQL 小内存配置**

**知识点**：P9 MySQL 小内存调优

创建 `deploy/mysql/low-memory.cnf`：

```ini
# 2GB 内存的服务器上，MySQL 8.4 的默认配置偏「大方」。这里把主要的几块内存开销压下来。
[mysqld]
performance_schema      = OFF        # 性能监控表，默认要占约 200MB 内存，个人项目用不上
innodb_buffer_pool_size = 128M       # InnoDB 缓存数据和索引的内存；数据量小，128M 足够
innodb_log_buffer_size  = 8M
max_connections         = 50         # 应用连接池最多 20 个连接，留足余量；默认 151 会预留更多内存
table_open_cache        = 400
tmp_table_size          = 16M
max_heap_table_size     = 16M
thread_cache_size       = 8
skip-name-resolve                    # 不对客户端做 DNS 反查，连接更快
character-set-server    = utf8mb4
collation-server        = utf8mb4_0900_ai_ci
```

官方 MySQL 镜像会自动读取 `/etc/mysql/conf.d/` 下的配置文件，编排文件里已经把它挂载到那里了。最大的一项节省是关掉 `performance_schema`，它默认会占用两百多 MB 内存，用来收集性能统计，个人项目用不上。

- [ ] **Step 4: 服务器 `.env` 的模板**

创建 `deploy/.env.production.example`：

```bash
# 服务器部署目录（/opt/question_set_program）里的 .env：从这个模板复制后填写。只放在服务器上，永远不要提交。
#
# 生成随机值：openssl rand -hex 16（密码）、openssl rand -hex 32（JWT 密钥）
# 密码只用十六进制随机串：它们会被拼进数据库连接串，@ : / # 这类字符会把连接串弄坏

# ---------- 当前线上的应用镜像：由 deploy.sh 自动维护，不要手改 ----------
IMAGE=

# ---------- MySQL 与 Redis（只在容器网络内部使用，不对外开放）----------
MYSQL_ROOT_PASSWORD=
MYSQL_PASSWORD=
REDIS_PASSWORD=

# ---------- 大模型（阿里云 DashScope）----------
API_KEY=
API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen-plus
EMBEDDING_MODEL=qwen3-vl-embedding

# ---------- 应用 ----------
JWT_SECRET_KEY=
APP_DEBUG=false
# 写成 https://你的公网IP
CORS_ALLOW_ORIGINS=
```

- [ ] **Step 5: 写部署脚本的测试**

**知识点**：P17 部署状态与回滚

创建 `deploy/test_deploy.sh`：

```bash
#!/usr/bin/env bash
# deploy.sh 的测试：用假的 docker 与 curl 代替真实命令（桩），在任何有 bash 的机器上都能跑，不需要 Docker。
#   bash deploy/test_deploy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO=crpi-demo-vpc.cn-hangzhou.personal.cr.aliyuncs.com/demo/question_set_program
OLD="$REPO:sha-aaaaaaa"
NEW="$REPO:sha-bbbbbbb"
failures=0

# 每个用例一个全新的临时目录：放一份 deploy.sh、一份 .env 和两个桩命令
setup() {
  WORK="$(mktemp -d)"
  cp "$SCRIPT_DIR/deploy.sh" "$WORK/"
  printf 'MYSQL_PASSWORD=x\nIMAGE=%s\nAPI_KEY=y\n' "${1:-}" > "$WORK/.env"
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

current_image() { sed -n 's/^IMAGE=//p' "$WORK/.env"; }

check() {
  local name="$1"; shift
  if "$@"; then echo "  通过：$name"; else echo "  失败：$name"; failures=$((failures + 1)); fi
}

echo "用例 1：新版本健康，记为当前版本"
setup "$OLD"; export HEALTHY="$OLD $NEW"
run_deploy "$NEW" && code=0 || code=$?
check "退出码为 0" test "$code" -eq 0
check ".env 里的 IMAGE 更新为新版本" test "$(current_image)" = "$NEW"
check ".env 的其他配置原样保留" grep -q '^API_KEY=y$' "$WORK/.env"
check "只拉取应用镜像，不碰其他镜像" grep -q "^docker compose pull app " "$WORK/calls.log"
check "部署后让 nginx 重新加载" grep -q "nginx -s reload" "$WORK/calls.log"

echo "用例 2：新版本不健康，回滚到上一个版本"
setup "$OLD"; export HEALTHY="$OLD"
run_deploy "$NEW" && code=0 || code=$?
check "退出码非 0，让流水线变红" test "$code" -ne 0
check "线上跑回了旧版本" test "$(cat "$WORK/running")" = "$OLD"
check ".env 里仍是旧版本" test "$(current_image)" = "$OLD"

echo "用例 3：第一次部署就失败，没有可回滚的版本"
setup ""; export HEALTHY=""
run_deploy "$NEW" && code=0 || code=$?
check "退出码非 0" test "$code" -ne 0
check ".env 里的 IMAGE 仍为空" test -z "$(current_image)"
check "提示没有可回滚的版本" grep -q "没有可回滚的版本" "$WORK/out.log"

echo "用例 4：镜像名不合法，直接拒绝"
setup "$OLD"; export HEALTHY="$NEW"
run_deploy "$REPO:latest; rm -rf /" && code=0 || code=$?
check "退出码为 2" test "$code" -eq 2
check "没有调用 docker" test ! -f "$WORK/calls.log"

echo "用例 5：部署目录里没有 .env，直接拒绝"
setup "$OLD"; rm "$WORK/.env"; export HEALTHY="$NEW"
run_deploy "$NEW" && code=0 || code=$?
check "退出码为 2" test "$code" -eq 2
check "没有调用 docker" test ! -f "$WORK/calls.log"
check "提示缺少 .env" grep -q "没有 .env" "$WORK/out.log"

echo
if [[ "$failures" -gt 0 ]]; then echo "$failures 项失败"; exit 1; fi
echo "全部通过"
```

如果你已经做过 CI/CD 计划的 Task 9，这个文件和 `deploy.sh` 都会被整体替换：当前版本改记在 `.env` 里，镜像地址改成 ACR 的格式，另外新增了用例 5。

Run: `bash deploy/test_deploy.sh`（Windows 下在 Git Bash 里执行）
Expected: 打印出「用例 1」的标题后，报 `cp: cannot stat '.../deploy/deploy.sh': No such file or directory`，退出码为 1

- [ ] **Step 6: 部署脚本**

**知识点**：P17 部署状态与回滚

创建 `deploy/deploy.sh`：

```bash
#!/usr/bin/env bash
# 在服务器上执行：拉取指定版本的应用镜像并替换运行中的容器；新版本通不过就绪检查时，自动回滚到上一个版本。
#
# 用法：bash deploy.sh <镜像>
#   例如 bash deploy.sh crpi-xxx-vpc.cn-hangzhou.personal.cr.aliyuncs.com/<命名空间>/question_set_program:sha-1a2b3c4
# 平时由 GitHub Actions 通过 SSH 调用；也可以登录服务器手动执行，回滚到指定旧版本时就是这么用的。
set -euo pipefail

IMAGE="${1:-}"
# 只接受「镜像仓库地址/路径:sha-7位提交号」：镜像名会拼进命令，先校验格式，杜绝注入
if [[ ! "$IMAGE" =~ ^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9._/-]+:sha-[0-9a-f]{7}$ ]]; then
  echo "镜像名不合法：'$IMAGE'（期望 <仓库地址>/<命名空间>/<仓库>:sha-<7位提交号>）" >&2
  exit 2
fi

# 以脚本所在目录为工作目录：docker compose 在这里找 docker-compose.yml 和 .env
cd "$(dirname "$0")"
if [[ ! -f .env ]]; then
  echo "当前目录没有 .env：请先按部署计划准备好配置文件" >&2
  exit 2
fi

READY_URL="${READY_URL:-http://127.0.0.1:8000/health/ready}"
HEALTH_RETRIES="${HEALTH_RETRIES:-60}"   # 默认每 2 秒检查一次，最多等 2 分钟（小内存机器上应用启动较慢）
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"

# 当前线上版本记在 .env 的 IMAGE= 里：平时直接执行 docker compose ps、logs 等命令也能用
previous="$(sed -n 's/^IMAGE=//p' .env | tail -n 1)"

record_image() {
  if grep -q '^IMAGE=' .env; then
    sed -i "s|^IMAGE=.*|IMAGE=$1|" .env
  else
    printf '\nIMAGE=%s\n' "$1" >> .env
  fi
}

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

# 命令行传入的 IMAGE 优先于 .env 里的值
start() {
  IMAGE="$1" docker compose up -d --remove-orphans
}

echo "拉取镜像 $IMAGE"
IMAGE="$IMAGE" docker compose pull app   # 只拉应用镜像；MySQL、Redis、Nginx 的镜像不变，不去访问 Docker Hub
start "$IMAGE"

if wait_ready; then
  record_image "$IMAGE"
  # 编排文件和 nginx 配置可能随这次部署更新了：让 nginx 平滑地重新加载（不中断连接）
  docker compose exec -T nginx nginx -s reload >/dev/null 2>&1 || echo "警告：nginx 重新加载失败，请检查 nginx 配置" >&2
  # 只清理没有标签的悬空镜像；旧版本的镜像要留着，回滚时用
  docker image prune -f >/dev/null
  echo "部署成功：$IMAGE"
  exit 0
fi

echo "新版本没有通过就绪检查，最近 100 行日志：" >&2
IMAGE="$IMAGE" docker compose logs --tail 100 app >&2 || true

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

和 CI/CD 计划里的版本相比：

- **当前版本记在 `.env` 的 `IMAGE=` 里**，而不是单独的状态文件。编排文件要求 `IMAGE` 必须有值，记在 `.env` 里之后，平时登录服务器直接执行 `docker compose ps`、`docker compose logs app`、`docker compose run certbot ...` 都能用，不用每次手动指定镜像
- **只拉取应用镜像**（`pull app`）：MySQL、Redis、Nginx 的镜像不会随部署变化，不必每次都去访问 Docker Hub，少一个失败的可能
- **成功后让 Nginx 重新加载**：部署时可能更新了 `nginx/default.conf`，重新加载才会生效。`nginx -s reload` 是平滑进行的，正在处理的连接不会中断
- **就绪检查默认等 2 分钟**：小内存机器上应用启动较慢

Run: `bash deploy/test_deploy.sh`
Expected: 5 个用例、16 项检查全部通过，最后一行是「全部通过」

- [ ] **Step 7: 续期与备份脚本**

**知识点**：P15 systemd timer、P16 备份

创建 `deploy/renew_cert.sh`：

```bash
#!/usr/bin/env bash
# 续期 HTTPS 证书。由 systemd 定时器每天执行两次（见 systemd/qsp-cert-renew.timer）。
# 是否真的续期由 certbot 自己判断：6 天有效期的证书，大约每 3 天才真正续一次，其余时候什么都不做。
set -euo pipefail
cd "$(dirname "$0")"

docker compose run --rm certbot renew --quiet
# 续期成功时，deploy-hook 已经把新证书复制进 certs/；让 nginx 平滑地重新加载，换上新证书
docker compose exec -T nginx nginx -s reload
```

创建 `deploy/backup_mysql.sh`：

```bash
#!/usr/bin/env bash
# 备份 MySQL，保留最近 7 天。由 systemd 定时器每天执行一次（见 systemd/qsp-backup.timer）。
set -euo pipefail
cd "$(dirname "$0")"

BACKUP_DIR=backups
KEEP_DAYS="${KEEP_DAYS:-7}"
mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/mysql-$(date +%Y%m%d-%H%M%S).sql.gz"
trap 'rm -f "$file.tmp"' EXIT   # 中途失败时，不留下半截的备份文件

# 在容器里执行 mysqldump，密码取自容器自己的环境变量，不出现在宿主机的命令行和进程列表里
# shellcheck disable=SC2016  # 单引号是有意的：$MYSQL_ROOT_PASSWORD 要在容器里展开，而不是在宿主机上
docker compose exec -T mysql sh -c \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --triggers --databases question_set' \
  | gzip > "$file.tmp"
mv "$file.tmp" "$file"

find "$BACKUP_DIR" -name 'mysql-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "备份完成：$file（$(du -h "$file" | cut -f1)）"
```

- **`trap ... EXIT`**：脚本退出时（无论成功还是失败）都会执行。先写到 `.tmp` 文件，成功后再改名，这样 `backups/` 目录里永远不会出现半截的备份文件
- **`--single-transaction`**：在一个事务里导出，得到一个一致的快照，而且不锁表，备份期间服务照常可用
- **`MYSQL_PWD`**：通过环境变量给 mysqldump 传密码，不写在命令行参数里，也就不会出现在进程列表中

- [ ] **Step 8: systemd 定时器**

**知识点**：P15 systemd timer 与 cron

创建 `deploy/systemd/qsp-cert-renew.service`：

```ini
[Unit]
Description=Renew HTTPS certificate for question_set_program
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
User=deploy
ExecStart=/usr/bin/bash /opt/question_set_program/renew_cert.sh
```

创建 `deploy/systemd/qsp-cert-renew.timer`：

```ini
[Unit]
Description=Check HTTPS certificate renewal twice a day

[Timer]
OnCalendar=*-*-* 03,15:17:00
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
```

创建 `deploy/systemd/qsp-backup.service`：

```ini
[Unit]
Description=Back up MySQL for question_set_program
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
User=deploy
ExecStart=/usr/bin/bash /opt/question_set_program/backup_mysql.sh
```

创建 `deploy/systemd/qsp-backup.timer`：

```ini
[Unit]
Description=Back up MySQL every day

[Timer]
OnCalendar=*-*-* 04:30:00
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
```

每个定时任务由两个文件组成：`.service` 描述「做什么」，`.timer` 描述「什么时候做」。和 cron 相比：
- **`Persistent=true`**：服务器关机期间错过的执行，开机后会补做一次，cron 不会
- **`RandomizedDelaySec`**：在设定的时间上随机推迟一段。全世界有无数服务器都在整点续期证书，错开一些对 Let's Encrypt 和你自己都好
- **日志统一**：`journalctl -u qsp-cert-renew` 就能看到每次执行的输出
- **`Requires/After=docker.service`**：确保 Docker 启动之后才执行

`Description` 用英文写，是为了避免个别终端显示中文时出现乱码。

- [ ] **Step 9: 让 CI 检查这些脚本**

在 `.github/workflows/ci.yml` 的 lint job 里，`hadolint` 那一步之前插入：

```yaml
      - name: shellcheck（部署脚本）
        run: shellcheck deploy/*.sh
```

在 test job 的最后（「上传测试报告」之后）追加：

```yaml
      - name: 部署脚本的测试
        run: bash deploy/test_deploy.sh
```

（如果之前做过 CI/CD 计划的 Task 9，这两步已经加过了，跳过。）

- [ ] **Step 10: 本地检查并提交**

```bash
shellcheck deploy/*.sh
bash deploy/test_deploy.sh
actionlint
git add deploy/ .github/workflows/ci.yml
git commit -m "feat: 生产部署文件（Compose 编排 Nginx/MySQL/Redis、部署回滚、证书续期、备份）"
git push origin backend
```

Expected: shellcheck 和 actionlint 没有输出，测试全部通过；推送后 CI 全部绿色。

---

## Task 4: 阿里云镜像仓库与自动构建

**目标**：在阿里云上建一个镜像仓库，让 GitHub Actions 在合并到 main 之后，自动构建镜像并推送进去。做完这个任务，仓库里就会有第一个可以部署的镜像，Task 5 就用它。

**Files:**
- Create: `.github/workflows/release.yml`（第一阶段：只构建和推送，还不部署）

**Interfaces:**
- Consumes: CI/CD 计划 Task 7 的 `ci.yml`（`workflow_call`）、Task 8 的 Dockerfile
- Produces:
  - 仓库变量：`ACR_REGISTRY`（公网地址）、`ACR_REGISTRY_VPC`（VPC 地址）、`ACR_NAMESPACE`
  - 仓库 Secrets：`ACR_USERNAME`、`ACR_PASSWORD`
  - 镜像 `<ACR_REGISTRY>/<ACR_NAMESPACE>/question_set_program:sha-<7位>`，以及 `:latest`
  - job 输出 `needs.image.outputs.tag`（Task 8 要用）

- [ ] **Step 1: 创建 ACR 个人版实例**

**知识点**：P11 镜像仓库的地域与地址

阿里云控制台 → 容器镜像服务 ACR：

1. 实例列表 → 创建个人版实例。**地域必须选服务器所在的地域**（比如服务器在华东1（杭州），这里也选杭州），否则服务器没法走 VPC 内网拉取。个人版要求账号是**个人实名认证**；如果你的账号是企业认证，只能用企业版（收费），或者换一个个人账号
2. 进入实例 → 仓库管理 → 访问凭证 → 设置固定密码
3. 命名空间 → 创建命名空间，名字只能用小写字母、数字、横线，比如 `qsp`；「自动创建仓库」选开启，「默认仓库类型」选**私有**
4. 在「访问凭证」页面，记下三样东西：
   - **公网地址**，形如 `crpi-xxxx.cn-hangzhou.personal.cr.aliyuncs.com`（GitHub Actions 推送时用）
   - **专有网络（VPC）地址**，形如 `crpi-xxxx-vpc.cn-hangzhou.personal.cr.aliyuncs.com`（服务器拉取时用）
   - **登录名**：阿里云账号全名

**为什么要分公网地址和 VPC 地址**：GitHub 的 runner 在国外，只能走公网推送。服务器在阿里云内部，走 VPC 地址时流量不出阿里云机房，速度快，也不产生公网流量费用。两个地址背后是同一个仓库。

- [ ] **Step 2: 在 GitHub 上录入凭据**

GitHub 仓库 → Settings → Secrets and variables → Actions：

| 类型 | 名称 | 值 |
|---|---|---|
| Repository secret | `ACR_USERNAME` | ACR 的登录名 |
| Repository secret | `ACR_PASSWORD` | Step 1 设置的固定密码 |
| Repository variable | `ACR_REGISTRY` | 公网地址（不带 `https://`） |
| Repository variable | `ACR_REGISTRY_VPC` | VPC 地址 |
| Repository variable | `ACR_NAMESPACE` | 命名空间，比如 `qsp` |

这几项放在仓库级别，而不是 `production` 环境里：构建镜像的 job 不属于任何环境，只能读到仓库级别的配置。

- [ ] **Step 3: 发布流水线（第一阶段）**

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

env:
  IMAGE_REPO: question_set_program

jobs:
  ci:
    name: 检查与测试
    if: inputs.image_tag == ''    # push 事件没有 inputs，表达式结果为空字符串，条件成立
    uses: ./.github/workflows/ci.yml

  image:
    name: 构建并推送镜像
    needs: ci
    runs-on: ubuntu-latest
    timeout-minutes: 40
    outputs:
      tag: ${{ steps.name.outputs.tag }}
    steps:
      - uses: actions/checkout@v7

      - name: 计算镜像名与标签
        id: name
        env:
          ACR_REGISTRY: ${{ vars.ACR_REGISTRY }}
          ACR_NAMESPACE: ${{ vars.ACR_NAMESPACE }}
        run: |
          image="${ACR_REGISTRY}/${ACR_NAMESPACE}/${IMAGE_REPO}"
          if [[ ! "$image" =~ ^[a-z0-9.-]+/[a-z0-9._-]+/[a-z0-9._-]+$ ]]; then
            echo "::error::镜像地址不合法：'$image'。检查仓库变量 ACR_REGISTRY、ACR_NAMESPACE（只能是小写）"
            exit 1
          fi
          echo "image=$image" >> "$GITHUB_OUTPUT"
          echo "tag=sha-${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"

      - uses: docker/setup-buildx-action@v4

      - name: 登录阿里云容器镜像服务（公网地址）
        uses: docker/login-action@v4
        with:
          registry: ${{ vars.ACR_REGISTRY }}
          username: ${{ secrets.ACR_USERNAME }}
          password: ${{ secrets.ACR_PASSWORD }}

      - name: 构建并推送
        uses: docker/build-push-action@v7
        with:
          context: .
          platforms: linux/amd64    # 与服务器的 CPU 架构一致（uname -m 输出 x86_64）
          push: true
          tags: |
            ${{ steps.name.outputs.image }}:${{ steps.name.outputs.tag }}
            ${{ steps.name.outputs.image }}:latest
          labels: |
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
            org.opencontainers.image.revision=${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

和 CI/CD 计划里的版本相比：
- 登录的是 ACR，用的是固定的用户名和密码，不再是 `GITHUB_TOKEN`，所以 job 不需要 `packages: write` 权限
- 显式指定 `platforms: linux/amd64`，和服务器的架构一致
- 镜像地址先做格式校验：命名空间写成大写之类的错误，在这里就能发现，而不是等到推送失败
- `timeout-minutes` 放宽到 40 分钟：第一次从国外推送到国内，全部镜像层都要上传，比较慢；之后只有代码层变化，通常一两分钟就能推完

- [ ] **Step 4: 本地检查、提交，合并后看镜像**

```bash
actionlint
git add .github/workflows/release.yml
git commit -m "feat: 发布流水线构建镜像并推送到阿里云 ACR"
git push origin backend
```

开一个 `backend → main` 的 PR，检查全部通过后合并。然后打开 Actions → Release，Expected:「检查与测试」和「构建并推送镜像」依次变绿。

到 ACR 控制台 → 镜像仓库 → `question_set_program` → 镜像版本，应当能看到 `sha-xxxxxxx` 和 `latest` 两个标签。**记下这个 `sha-` 标签，Task 5 要用。**

---

## Task 5: 首次部署：数据库与应用

**目标**：把 Task 3 的文件放到服务器上，先用一张自签名证书把整套服务跑起来。这时浏览器会提示证书不受信任，这是预期的，Task 6 会换成正式证书。

**为什么先用自签名证书（P13）**：这是一个「先有鸡还是先有蛋」的问题。Nginx 的配置里写了证书文件，文件不存在时 Nginx 起不来；可是申请正式证书时，又需要 Nginx 已经在 80 端口上提供验证文件。解决办法是先放一张自签名证书占位，让 Nginx 能启动；拿到正式证书后，再把它替换掉。

**Files:** 无（在服务器上操作）

- [ ] **Step 1: 把部署文件传到服务器**

在**本机**、仓库根目录下执行（「你的公网IP」换成实际的 IP）：

```bash
scp -r deploy/docker-compose.yml deploy/.env.production.example deploy/deploy.sh deploy/renew_cert.sh \
  deploy/backup_mysql.sh deploy/nginx deploy/mysql deploy/systemd root@你的公网IP:/opt/question_set_program/
```

在服务器上把文件交给 `deploy` 用户，然后切换过去：

```bash
sudo chown -R deploy:deploy /opt/question_set_program
sudo -iu deploy
cd /opt/question_set_program
ls
```

之后本任务的所有命令，都以 `deploy` 用户的身份在这个目录下执行。

- [ ] **Step 2: 填写 `.env`**

```bash
cp .env.production.example .env
chmod 600 .env
echo "MYSQL_ROOT_PASSWORD=$(openssl rand -hex 16)"
echo "MYSQL_PASSWORD=$(openssl rand -hex 16)"
echo "REDIS_PASSWORD=$(openssl rand -hex 16)"
echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"
nano .env
```

把上面打印出来的四个随机值填进 `.env` 对应的位置，再填上 `API_KEY`，以及 `CORS_ALLOW_ORIGINS=https://你的公网IP`。`IMAGE=` 保持为空，部署脚本会自己填。

- MySQL 和 Redis 的密码在**第一次启动时**就会写进数据卷，之后再改 `.env` 不会生效（要到数据库里改）。所以现在就用随机值，不要先填个简单的「以后再改」
- `JWT_SECRET_KEY` 必须是新生成的，不能和本机开发用的相同

- [ ] **Step 3: 生成自签名的占位证书，并建好目录**

**知识点**：P13 证书引导

```bash
PUBLIC_IP=你的公网IP
mkdir -p certs certbot-www letsencrypt backups
openssl req -x509 -newkey rsa:2048 -nodes -days 7 \
  -subj "/CN=${PUBLIC_IP}" -addext "subjectAltName=IP:${PUBLIC_IP}" \
  -keyout certs/privkey.pem -out certs/fullchain.pem
openssl x509 -in certs/fullchain.pem -noout -subject -ext subjectAltName
```

Expected: 输出里有 `IP Address:你的公网IP`。

目录要在启动容器之前由 `deploy` 用户建好。如果目录不存在，Docker 挂载时会以 root 身份自动创建，之后 `deploy` 用户就没法往里写文件了。

- [ ] **Step 4: 登录镜像仓库（VPC 地址）**

**知识点**：P11

```bash
docker login --username=ACR登录名 ACR的VPC地址
```

输入 Task 4 设置的固定密码，看到 `Login Succeeded` 即可。登录信息会保存在 `/home/deploy/.docker/config.json`，之后部署时拉镜像都靠它，只需要登录这一次。Docker 会提示凭据以未加密的形式保存，这是正常的：这个文件只有 `deploy` 用户能读。

- [ ] **Step 5: 第一次部署**

```bash
bash deploy.sh ACR的VPC地址/命名空间/question_set_program:sha-Task4记下的标签
```

第一次执行会依次做这些事：拉取应用镜像 → 启动 MySQL，初始化数据目录（最慢的一步，一两分钟）→ 启动 Redis → 两者都健康之后启动应用，应用启动时自动建表 → 启动 Nginx → 就绪检查通过后，把镜像写进 `.env`。

Expected: 最后一行是 `部署成功：...`。

```bash
docker compose ps
grep '^IMAGE=' .env
```

Expected: mysql、redis、app 的状态是 `running (healthy)`，nginx 是 `running`；`.env` 里的 `IMAGE=` 已经填上了。

**如果失败**：
- `deploy.sh` 会自动打印应用最近 100 行日志，先看这里
- 看 MySQL 的日志：`docker compose logs mysql | tail -50`
- 最常见的原因：`.env` 里有空值（compose 会提示是哪个变量），或者 `API_KEY` 没填

- [ ] **Step 6: 验证整条链路**

```bash
curl -s http://127.0.0.1:8000/health/ready; echo           # 绕过 Nginx，直接访问应用
curl -sk https://127.0.0.1/health/ready; echo              # 经过 Nginx 和 HTTPS（-k 表示暂时接受自签名证书）
curl -sI http://127.0.0.1/ | head -3                       # HTTP 应当 301 跳转到 https
```

Expected: 前两条都输出 `{"status":"ok","checks":{"redis":"ok","database":"ok"}}`；第三条是 `HTTP/1.1 301 Moved Permanently`。

在**本机**浏览器里打开 `https://你的公网IP/docs`：浏览器会提示「连接不是私密连接」，这是自签名证书导致的，属于预期。点「高级 → 继续访问」，应当能看到 API 文档页面。

- [ ] **Step 7: 实测内存占用**

**知识点**：P1 资源规划

```bash
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}'
free -h
```

Expected: 每个容器的用量都明显低于它的上限，`free` 里 `available` 一栏还剩几百 MB。如果应用的内存用量接近 800MB，把这个结果告诉我，需要调整上限或者减少应用的内存占用。

---

## Task 6: 申请 IP 证书，换成受信任的 HTTPS

**目标**：用 Certbot 从 Let's Encrypt 申请正式证书，替换掉自签名证书。做完之后，浏览器访问 `https://你的公网IP` 就不会再有警告。

**知识点**：P12 HTTPS 证书、P14 频率限制

**背景**：浏览器之所以信任一张证书，是因为它由浏览器内置信任的证书颁发机构（CA）签发。Let's Encrypt 是免费的 CA，通过 ACME 协议自动签发证书：它先要求你证明「这个 IP 确实归你控制」，方法是在 80 端口上放一个它指定的文件（HTTP-01 验证），验证通过后才签发。

**IP 证书的两个特殊之处**：
1. 必须申请 `shortlived` 配置，有效期约 6 天，所以必须完全自动化续期（Task 7）
2. 浏览器用 IP 访问时不会发送 SNI（服务器名称指示，一种告诉服务器「我要访问哪个网站」的 TLS 扩展）。所以服务器端必须在「不知道客户端要访问谁」的情况下，也能拿出正确的证书。Nginx 的 `default_server` 恰好就是这样工作的。这也是本计划选择 Nginx 而不是 Caddy 的原因：编写时查到，Caddy 在这两个问题上都有尚未解决的已知问题（caddyserver/caddy#7399，以及云服务器上公网 IP 走 NAT 导致的证书匹配失败）

**Files:** 无（在服务器上操作）

- [ ] **Step 1: 先确认外网能访问到验证路径**

在服务器上（`deploy` 用户，`/opt/question_set_program` 目录）：

```bash
mkdir -p certbot-www/.well-known/acme-challenge
echo ok > certbot-www/.well-known/acme-challenge/probe
```

在**本机**（不要在服务器上执行，必须从外网访问）：

```bash
curl -s http://你的公网IP/.well-known/acme-challenge/probe
```

Expected: 输出 `ok`。这证明 80 端口从公网能访问到，Nginx 也配置对了。确认之后删掉测试文件：`rm certbot-www/.well-known/acme-challenge/probe`。

**如果不是 `ok`**：
- 连接超时：安全组没放行 80 端口（Task 1 Step 4）
- 返回的是一个阿里云的提示页面：说明 80 端口被备案检查拦截了，见本任务末尾的「替代方案」

- [ ] **Step 2: 演练一次（不签发真证书）**

**知识点**：P14 频率限制与演练

```bash
PUBLIC_IP=你的公网IP
docker compose run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  --preferred-profile shortlived \
  --ip-address "$PUBLIC_IP" \
  --cert-name question-set \
  --non-interactive --agree-tos \
  --dry-run
```

Expected: 最后输出 `The dry run was successful.`

`--dry-run` 会向 Let's Encrypt 的**测试环境**（staging）申请一张不受信任的证书，而且不保存到磁盘。它会走完整个验证流程，但不占用正式环境的频率额度。正式环境的限制是：同一组标识每 7 天最多签发 5 张。如果在正式环境里反复试错，很容易被锁上一周。**所以永远先用 `--dry-run` 跑通，再申请正式证书。**

- [ ] **Step 3: 申请正式证书**

```bash
docker compose run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  --preferred-profile shortlived \
  --ip-address "$PUBLIC_IP" \
  --cert-name question-set \
  --deploy-hook 'cp "$RENEWED_LINEAGE/fullchain.pem" "$RENEWED_LINEAGE/privkey.pem" /etc/nginx-certs/' \
  --non-interactive --agree-tos
docker compose exec -T nginx nginx -s reload
```

Expected: 输出 `Successfully received certificate.`，证书的过期时间大约在 6 天以后。

`--deploy-hook` 这条命令会在**每次**成功签发之后执行，包括以后的每一次续期（Certbot 会把它记在续期配置里）。它把新证书从 Certbot 的目录复制到 Nginx 读取的 `certs/` 目录，覆盖掉自签名证书。`$RENEWED_LINEAGE` 由 Certbot 提供，指向 `/etc/letsencrypt/live/question-set`。命令外面用单引号，是为了让这个变量在 Certbot 的容器里展开，而不是在你当前的 shell 里。

- [ ] **Step 4: 验证**

在**本机**：

```bash
curl -sv https://你的公网IP/health 2>&1 | grep -E "SSL certificate verify|issuer:|expire date:"
curl -s https://你的公网IP/health/ready; echo
```

Expected:
- `SSL certificate verify ok.`
- `issuer:` 那一行里有 Let's Encrypt（中间证书的名字会变，只要是 Let's Encrypt 签发的即可）
- `expire date:` 大约在 6 天后
- 第二条输出 `{"status":"ok",...}`

用浏览器打开 `https://你的公网IP/docs`：地址栏显示锁形图标，不再有警告。

**替代方案：如果 80 端口被拦截，或者签发一直失败**

- 可以先继续使用自签名证书：通信仍然是加密的，只是浏览器会提示证书不受信任。用 curl 调接口时加 `-k`，或者把证书导入本机的信任列表
- 长期来看，最稳妥的办法是买一个域名、在阿里云完成 ICP 备案，再改用基于域名的证书

---

## Task 7: 定时任务：证书续期与数据库备份

**目标**：证书约 6 天就过期，必须全自动续期。数据库也要每天自动备份。

**Files:** 无（在服务器上操作）

- [ ] **Step 1: 先手动演练一次续期**

在服务器上（`deploy` 用户）：

```bash
cd /opt/question_set_program
docker compose run --rm certbot renew --dry-run
```

Expected: `Congratulations, all simulated renewals succeeded`。

- [ ] **Step 2: 安装定时器**

**知识点**：P15 systemd timer

systemd 的单元文件需要 root 权限才能安装，所以切换回你的管理员账号执行：

```bash
sudo cp /opt/question_set_program/systemd/qsp-*.service /opt/question_set_program/systemd/qsp-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qsp-cert-renew.timer qsp-backup.timer
systemctl list-timers 'qsp-*'
```

Expected: 列出两个定时器，`NEXT` 一列是下一次执行的时间。

- [ ] **Step 3: 手动触发一次，确认真的能跑通**

```bash
sudo systemctl start qsp-cert-renew.service
sudo systemctl status qsp-cert-renew.service --no-pager | head -5
journalctl -u qsp-cert-renew.service -n 20 --no-pager

sudo systemctl start qsp-backup.service
journalctl -u qsp-backup.service -n 5 --no-pager
ls -lh /opt/question_set_program/backups/
```

Expected:
- 续期服务的状态是 `inactive (dead)`，并且显示 `status=0/SUCCESS`。证书还没到续期时间，所以这次什么都没做，这是正常的
- 备份日志里有 `备份完成：backups/mysql-....sql.gz`，目录里有这个文件

- [ ] **Step 4: 过几天确认续期真的发生了**

证书剩余一半有效期（约 3 天）时，Certbot 才会真正续期。部署后第 4 天左右，执行：

```bash
docker compose run --rm certbot certificates
```

Expected: `Expiry Date` 比最初签发时晚了几天，说明续期成功了。如果到期日没有变化，查看 `journalctl -u qsp-cert-renew.service` 里的报错。**这一步一定要做**：6 天的证书一旦续期失败，最多一周后 HTTPS 就会中断。

- [ ] **Step 5: 开启磁盘快照**

**知识点**：P16 备份

`backups/` 和数据库在同一块磁盘上，磁盘坏了会一起丢失。阿里云控制台 → 云服务器 ECS → 快照 → 自动快照策略，给系统盘设置一个每天一次、保留 7 天的策略（按快照占用的容量计费，数据量小的话费用很低）。

快照还能覆盖 `mysqldump` 管不到的数据，比如 Chroma 向量库的数据卷。

---

## Task 8: 自动部署

**目标**：之后每次合并到 main，GitHub Actions 都会自动完成「构建镜像 → 部署到服务器 → 就绪检查 → 失败自动回滚」。

**Files:**
- Modify: `.github/workflows/release.yml`（追加 deploy job）

**Interfaces:**
- Consumes: Task 3 的 `deploy.sh`；Task 4 的 `needs.image.outputs.tag`、`ACR_REGISTRY_VPC`、`ACR_NAMESPACE`；Task 5 服务器上已经完成的 ACR 登录
- Produces: GitHub 环境 `production`：Secrets `DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS`；Variables `DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_PORT`、`DEPLOY_PATH`、`PRODUCTION_URL`

- [ ] **Step 1: 部署专用的 SSH 密钥**

**知识点**：CI/CD 计划 K23（SSH 密钥部署与主机指纹校验）

在**本机**生成一对新密钥，专门给 GitHub Actions 用：

```bash
ssh-keygen -t ed25519 -f qsp_deploy -C "github-actions-deploy" -N ""
```

把公钥装到 `deploy` 用户下。在服务器上，用管理员账号执行：

```bash
sudo -u deploy mkdir -p /home/deploy/.ssh
sudo -u deploy chmod 700 /home/deploy/.ssh
echo '这里粘贴 qsp_deploy.pub 文件的内容' | sudo -u deploy tee -a /home/deploy/.ssh/authorized_keys
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

在**本机**获取服务器的主机指纹，并核对：

```bash
ssh-keyscan -p 22 你的公网IP 2>/dev/null > known_hosts_line
ssh-keygen -lf known_hosts_line                     # 本机看到的指纹
ssh root@你的公网IP 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'   # 服务器上的真实指纹
```

两边 ed25519 那一行的 SHA256 指纹必须一致。然后验证能以 `deploy` 用户登录：

```bash
ssh -i qsp_deploy -o UserKnownHostsFile=known_hosts_line deploy@你的公网IP "cd /opt/question_set_program && docker compose ps"
```

- [ ] **Step 2: GitHub 的 `production` 环境**

GitHub 仓库 → Settings → Environments → New environment，名称填 `production`：

1. Deployment branches and tags：只允许 `main`
2. （可选）Required reviewers：勾选自己，每次部署前要在网页上点「Approve」
3. 添加下面这些：

| 类型 | 名称 | 值 |
|---|---|---|
| Secret | `DEPLOY_SSH_KEY` | `qsp_deploy` 私钥文件的完整内容 |
| Secret | `DEPLOY_KNOWN_HOSTS` | `known_hosts_line` 文件的内容 |
| Variable | `DEPLOY_HOST` | 你的公网 IP |
| Variable | `DEPLOY_USER` | `deploy` |
| Variable | `DEPLOY_PORT` | `22` |
| Variable | `DEPLOY_PATH` | `/opt/question_set_program` |
| Variable | `PRODUCTION_URL` | `https://你的公网IP` |

录完之后，删掉本机的 `qsp_deploy`（私钥）和 `known_hosts_line`。

- [ ] **Step 3: 在发布流水线末尾追加 deploy job**

**知识点**：P17、CI/CD 计划 K25（job 间传值）、K26（表达式注入）

在 `.github/workflows/release.yml` 末尾（`image` job 之后）追加：

```yaml
  deploy:
    name: 部署到生产环境
    needs: image
    # image 被跳过（手动回滚）时也要部署；其余情况必须 image 成功
    if: >-
      !cancelled() &&
      (needs.image.result == 'success' ||
       (needs.image.result == 'skipped' && inputs.image_tag != ''))
    runs-on: ubuntu-latest
    timeout-minutes: 20
    environment:
      name: production
      url: ${{ vars.PRODUCTION_URL }}
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
          ACR_REGISTRY_VPC: ${{ vars.ACR_REGISTRY_VPC }}
          ACR_NAMESPACE: ${{ vars.ACR_NAMESPACE }}
        run: |
          tag="${INPUT_TAG:-$BUILT_TAG}"
          if [[ ! "$tag" =~ ^sha-[0-9a-f]{7}$ ]]; then
            echo "::error::镜像标签不合法：'$tag'，应形如 sha-1a2b3c4"
            exit 1
          fi
          # 服务器和镜像仓库在同一地域：用 VPC 内网地址拉取，快，而且不产生公网流量
          echo "image=${ACR_REGISTRY_VPC}/${ACR_NAMESPACE}/${IMAGE_REPO}:${tag}" >> "$GITHUB_OUTPUT"

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

      - name: 上传编排文件与脚本
        # 只传仓库里的部署文件；服务器上的 .env、证书和数据不受影响
        run: |
          scp -r deploy/docker-compose.yml deploy/deploy.sh deploy/renew_cert.sh deploy/backup_mysql.sh \
            deploy/nginx deploy/mysql "deploy-target:${DEPLOY_PATH}/"

      - name: 部署（失败会自动回滚）
        env:
          IMAGE: ${{ steps.target.outputs.image }}
        run: |
          # ssh 把整条命令作为一个字符串交给服务器的 shell，变量在 runner 上展开是有意的（SC2029）；
          # 能这样做的前提是值可信：镜像名刚刚校验过格式，部署路径来自仓库变量
          # shellcheck disable=SC2029
          ssh deploy-target "bash '${DEPLOY_PATH}/deploy.sh' '${IMAGE}'"

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

它和 CI/CD 计划 Task 12 里的 deploy job 基本一样，那里有逐段解释。区别只有两点：
1. 不再临时登录 GHCR：服务器在 Task 5 已经登录过 ACR 的 VPC 地址，登录信息一直有效
2. 除了 `docker-compose.yml` 和 `deploy.sh`，续期脚本、备份脚本、`nginx/`、`mysql/` 也一起上传，这样修改 Nginx 配置之后，合并到 main 就能生效（`deploy.sh` 成功后会让 Nginx 重新加载）。服务器上的 `.env`、证书和数据不会被覆盖

- [ ] **Step 4: 提交并发布**

```bash
actionlint
git add .github/workflows/release.yml
git commit -m "feat: 发布流水线自动部署到阿里云 ECS"
git push origin backend
```

开 PR、合并。Actions → Release 里三个 job 依次变绿。在服务器上确认：

```bash
grep '^IMAGE=' /opt/question_set_program/.env   # 已经是这次合并对应的 sha- 标签
```

- [ ] **Step 5: 演练一次回滚**

**知识点**：P17

1. Actions → Release → Run workflow，`image_tag` 填 Task 4 的第一个标签
2. Expected: 「检查与测试」和「构建并推送镜像」显示为跳过，「部署到生产环境」成功，服务器上 `.env` 的 `IMAGE=` 变回旧标签
3. 再运行一次，填最新的标签，恢复到最新版本

GitHub 出故障、流水线用不了的时候，也可以登录服务器直接回滚：

```bash
sudo -iu deploy
cd /opt/question_set_program
bash deploy.sh ACR的VPC地址/命名空间/question_set_program:sha-旧标签
```

---

## Task 9: 运维手册与文档

**目标**：把日常会用到的操作记录下来，并更新 CLAUDE.md。

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 演练一次数据恢复**

**知识点**：P16 恢复演练

没有演练过恢复的备份，不能算作备份。在服务器上（`deploy` 用户）：

```bash
cd /opt/question_set_program
latest=$(ls -t backups/mysql-*.sql.gz | head -1)
gunzip -c "$latest" | grep -c 'CREATE TABLE'    # 应当等于数据库里的表数量
```

**真正需要恢复时**（会用备份覆盖当前数据）：

```bash
docker compose stop app                            # 先停应用，恢复期间不能有新的写入
gunzip -c backups/要恢复的文件.sql.gz | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot'
docker compose start app
```

- [ ] **Step 2: 更新 CLAUDE.md**

在 `CLAUDE.md` 末尾加入下面这一节。如果已经按 CI/CD 计划 Task 13 加过「CI/CD」一节，就用这一节替换它：

````markdown
## CI/CD 与部署

生产环境：阿里云 ECS（中国内地、2GB、x86_64），单机 Docker Compose，通过公网 IP 用 HTTPS 访问。

| Workflow | 触发 | 做什么 |
|---|---|---|
| `.github/workflows/ci.yml` | 指向 main 的 PR；推送到 main 以外的分支；被 release.yml 调用 | 静态检查、带 Redis 的后端测试、前端构建；PR 上另外检查镜像能否构建 |
| `.github/workflows/release.yml` | 推送到 main；手动运行（回滚） | 调用 ci.yml → 构建镜像推到阿里云 ACR → SSH 部署，就绪检查失败自动回滚 |
| `.github/workflows/security.yml` | 每周一；手动 | 依赖漏洞扫描（不阻塞合并） |

服务器目录 `/opt/question_set_program`（`deploy` 用户）：`docker-compose.yml` 编排 nginx、app、mysql、redis，外加按需运行的 certbot；`.env` 保存全部密钥，其中的 `IMAGE=` 由 `deploy.sh` 维护，记录当前线上版本。

常用操作（在服务器上，以 `deploy` 用户身份、在部署目录下执行）：

```bash
docker compose ps                          # 各服务状态
docker compose logs -f --tail 100 app      # 应用日志
docker stats --no-stream                   # 内存占用
docker compose up -d                       # 修改 .env 后让配置生效（会重建受影响的容器）
bash deploy.sh <镜像>                       # 部署或回滚到指定版本
docker compose run --rm certbot certificates   # 查看证书到期时间
systemctl list-timers 'qsp-*'              # 证书续期、备份定时器
```

约定：
- 对公网只开放 22、80、443；MySQL、Redis 不发布端口，应用端口只绑定在 127.0.0.1
- HTTPS 证书是 Let's Encrypt 的 IP 证书（约 6 天有效期），由 `qsp-cert-renew.timer` 每天检查两次续期；续期后，deploy-hook 把证书复制到 `certs/`
- 新增必填配置项时，同步更新 `backend/.env.example`、`deploy/.env.production.example`、`ci.yml` 里的占位 `env`，以及服务器上的 `.env`
- 生产环境 `APP_DEBUG=false`；`CORS_ALLOW_ORIGINS` 设为 `https://<公网IP>`
- 修改 `deploy/nginx/default.conf` 后，合并到 main 即可生效（部署成功后会让 nginx 重新加载）；修改 `deploy/mysql/low-memory.cnf` 后，需要手动执行 `docker compose restart mysql`
- 数据库每天 04:30 左右备份到 `backups/`，保留 7 天；系统盘另有阿里云自动快照
- 接入 RAG 时要给 `/app/backend/rag_db`、`rag_uploads`、`rag_eval` 加数据卷，否则每次部署都会清空知识库
````

- [ ] **Step 3: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 补充生产部署结构与运维约定"
```

---

## 验收对照表

| # | 要求 | 由谁保证 | 怎么确认 |
|---|---|---|---|
| D-R1 | 受信任的 HTTPS | Task 6 | 本机执行 `curl -sv https://IP/health` 显示 `SSL certificate verify ok`；浏览器没有警告 |
| D-R2 | HTTP 跳转 HTTPS | Task 3 的 nginx 配置 | `curl -sI http://IP/` 返回 301 |
| D-R3 | 证书自动续期 | Task 7 | 第 4 天左右，`certbot certificates` 显示的到期日往后推了 |
| D-R4 | 只开放三个端口 | Task 1 的安全组、Task 3 的编排 | 在本机 PowerShell 里执行 `3306, 6379, 8000 \| % { Test-NetConnection 你的公网IP -Port $_ \| select RemotePort, TcpTestSucceeded }`，三个都是 False |
| D-R5 | 2GB 内存稳定运行 | Task 1 的 swap、Task 3 的内存上限 | `docker stats` 各容器都低于上限；`free -h` 里 swap 用量很少 |
| D-R6 | 生产环境关闭调试模式 | Task 2 | `test_app_settings.py`；服务器 `.env` 里是 `APP_DEBUG=false` |
| D-R7 | 自动部署与回滚 | Task 3、8 | `test_deploy.sh`；Task 8 Step 5 的回滚演练 |
| D-R8 | 每日备份 | Task 7 | `backups/` 里每天有新文件；Task 9 Step 1 的恢复演练 |
| D-R9 | 重启后自动恢复 | `restart: unless-stopped`、定时器 enable | 执行 `sudo reboot`，等 2 分钟后本机访问 `https://IP/health/ready` 返回 ok |
