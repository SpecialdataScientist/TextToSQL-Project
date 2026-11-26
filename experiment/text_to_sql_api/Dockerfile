FROM python:3.10-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive

ENV TZ=Asia/Seoul \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
        tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app/text_to_sql_api

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p embedded_schema embedded_schema_open_ai rdf_triplet

EXPOSE 80

CMD ["/bin/bash"]
