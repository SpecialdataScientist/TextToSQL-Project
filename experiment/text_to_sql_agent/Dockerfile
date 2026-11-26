FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_HOST=0.0.0.0 \
    AGENT_PORT=31067

WORKDIR /app/text_to_sql_agent
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY main.py ./
COPY agent_server/ ./agent_server/

EXPOSE 80
CMD ["/bin/bash"]
