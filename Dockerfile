# syntax=docker/dockerfile:1
# @Description : 多阶段构建（builder 装 venv 依赖 -> runtime 精简运行）
# 用法：docker build -t fastsample . && docker run -p 8080:8080 fastsample

# ---------- 构建阶段：依赖安装到独立 venv ----------
FROM python:3.12-slim-bookworm AS builder

ENV LANG=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# ---------- 运行阶段 ----------
FROM python:3.12-slim-bookworm

ENV LANG=C.UTF-8 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH" \
    HTTP_API_LISTEN_HOST=0.0.0.0

WORKDIR /app

# 非 root 运行（logs 目录供 loguru 文件 sink 写入，可挂 volume 持久化）
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/logs \
    && chown appuser:appuser /app/logs

COPY --from=builder /opt/venv /opt/venv
COPY src/ src/
COPY main.py pyproject.toml ./

USER appuser
EXPOSE 8080

# 存活探针：liveness 端点不检查下游依赖，适合容器级健康判定
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/sample/monitor/healthz', timeout=3).status == 200 else 1)"

CMD ["python", "main.py", "run"]
