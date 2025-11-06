FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY src /app/src
COPY entrypoint.sh /entrypoint.sh
COPY README.md LICENSE /app/

RUN chmod +x /entrypoint.sh

ENV CONFIG_PATH=/config/sports.yaml \
    PROCESS_INTERVAL=0 \
    RUN_ONCE=true \
    DRY_RUN=false

ENV PYTHONPATH=/app/src

ENTRYPOINT ["/entrypoint.sh"]