FROM python:3.11-slim

WORKDIR /app

# System deps for earthengine-api (cryptography / grpc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libssl-dev && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Backend source (build context = repo root)
COPY backend/ .

# Frontend static files
COPY frontend/ /app/static/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
