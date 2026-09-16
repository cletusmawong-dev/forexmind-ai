# ForexMind AI backend — persistent FastAPI service.
# Works on Render / Railway / Fly.io / HF Spaces / any Docker host.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data

ENV PYTHONUNBUFFERED=1 \
    REPLAY_ENABLED=1

EXPOSE 8000

# Health check: GET /api/health
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
