FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_PATH=/data/strata.db

WORKDIR /srv

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# 单 worker + 多线程：偏序引擎缓存驻留内存，写操作需在同一进程内串行化；
# --chdir 到应用目录，使 app.py 作为顶层模块 app 被加载，
# 其 `import storage` / `from engine...` 等导入随之可解析
CMD ["gunicorn", "--chdir", "/srv/app", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", "--timeout", "60", "app:app"]
