FROM postgres:17-bookworm

ENV PATH="/opt/seed-venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /opt/text-to-sql-db/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && python3 -m venv /opt/seed-venv \
    && python -m pip install --no-cache-dir -r /opt/text-to-sql-db/requirements.txt \
    && rm -rf /var/lib/apt/lists/*

COPY seed_db /opt/text-to-sql-db/seed_db
COPY dataset/db_schema.json /opt/text-to-sql-db/dataset/db_schema.json
COPY docker/seed-db /usr/local/bin/seed-db
COPY docker/10-seed.sh /docker-entrypoint-initdb.d/10-seed.sh
RUN chmod 0755 /usr/local/bin/seed-db /docker-entrypoint-initdb.d/10-seed.sh

EXPOSE 80
