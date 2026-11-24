FROM docker.m.daocloud.io/library/python:3.11-slim
LABEL authors="sunzw"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1

RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g; s|http://security.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g; s|http://security.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r /app/requirements.txt \
    && pip install -i https://pypi.tuna.tsinghua.edu.cn/simple uv

ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_CACHE_DIR=/opt/uv-cache

RUN useradd -m -u 1000 -s /bin/bash appuser \
    && mkdir -p /app /opt/uv-cache /app/backend/logs \
    && chown -R appuser:appuser /app /opt/uv-cache

USER appuser
WORKDIR /app


# 代码 + 运行时数据一起进镜像（chroma_db / checkpoints / uploads / corpus）
COPY --chown=appuser:appuser backend ./backend

# 构建期把 uvx 的隔离环境装完，运行期才能秒起。
# 不要用 `<pkg> --help` 预热：打印完帮助就退出，依赖只被解析、没有装全。
# --from <pkg> python -c 会先建好含全部依赖的环境，再执行 python。
# 不加 || true：预热失败就让构建失败，比运行期静默少 18 个工具好排查。
# 版本约束必须与 app/mcp/client.py 的 _AMAP_PYDANTIC_PIN 一致。
RUN timeout 900 uvx --with "pydantic>=2.9,<2.14" --from amap-mcp-server python -c "print('amap mcp ready')"
RUN timeout 300 uvx --from mcp-server-time python -c "print('time mcp ready')"

WORKDIR /app/backend

# --workers 1 不能改：BM25 索引、MCP 子进程、SQLite checkpointer
# 全是进程内状态，多 worker 会各建一份并并发写同一个 db
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# 用 python 自带 urllib，不装 curl（curl 会额外拉 krb5/ldap/gnutls 一整套）
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"
