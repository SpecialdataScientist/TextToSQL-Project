FROM python:3.10-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive

ENV TZ=Asia/Seoul \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=31068

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
        tzdata && \
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime && \
    echo "$TZ" > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app/text_to_sql_mcp

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY server.py ./
COPY column_mcp/ ./column_mcp/
COPY dataset/db_schema.json dataset/column_descriptions.json ./dataset/

EXPOSE 80

CMD ["/bin/bash"]
