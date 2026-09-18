FROM python:3.12-slim

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Reuses the same `app.*` package (ipir, engines/oracle) as the API/worker
# image — see docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md section
# 19, which nests `rating_engine/` as a sibling of `app/` under `backend/`.
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
COPY backend/rating_engine ./rating_engine
COPY data ./data

RUN pip install --no-cache-dir .

EXPOSE 8080

# This service is private/authenticated only when deployed (locked doc
# section 12.2) — Cloud Run IAM invoker binding is applied at deploy time,
# not by this Dockerfile. See docs/implementation/DECISIONS.md (D3): actual
# deployment configuration is a later checkpoint (CP11), not this session.
CMD exec uvicorn rating_engine.main:app --host 0.0.0.0 --port ${PORT:-8080}
