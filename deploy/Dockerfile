FROM python:3.13-slim
WORKDIR /app
COPY deploy/requirements-assistant.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home assistant
COPY src/zotero_arxiv_daily /app/src/zotero_arxiv_daily
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 ASSISTANT_DB=/data/assistant.sqlite3
RUN mkdir /data && chown assistant /data
USER assistant
EXPOSE 8080
CMD ["uvicorn", "zotero_arxiv_daily.assistant.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
