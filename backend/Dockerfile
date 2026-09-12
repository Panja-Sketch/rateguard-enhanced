FROM python:3.12-slim

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml and source files from repository root build context
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
COPY data ./data

# Install Python package dependencies
RUN pip install --no-cache-dir .

EXPOSE 8000

# Default command for Cloud Run ($PORT)
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
