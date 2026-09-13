# ForexMind AI backend — root Dockerfile.
# (Render's API-created services default to context "." — this file lets the
# service build without overriding the context. Same image as backend/Dockerfile.)
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/data ./data

ENV PYTHONUNBUFFERED=1 \
    REPLAY_ENABLED=1

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
