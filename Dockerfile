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