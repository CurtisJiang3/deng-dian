FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/tibo.sqlite3

WORKDIR /app
RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin notifier \
    && mkdir /data \
    && chown notifier:notifier /data
COPY tibo_feishu.py /app/tibo_feishu.py

USER notifier
VOLUME ["/data"]
CMD ["python", "tibo_feishu.py", "run"]
