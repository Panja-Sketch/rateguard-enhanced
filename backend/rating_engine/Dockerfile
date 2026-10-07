# RateGuard Demo Insurer Rating Engine (black-box REST reference engine).
#
# Independent image: it contains ONLY `backend/rating_engine` and its own pinned
# requirements. It does not copy RateGuard's `app` package or the `data/`
# directory, so it cannot import RateGuard's oracle, IPIR, workbook compiler,
# test planner or decision code even by accident.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Git commit this image was built from (passed by cloudbuild.yaml); reported
# through /capabilities as non-sensitive provenance.
ARG ENGINE_SOURCE_COMMIT=unknown
ENV ENGINE_SOURCE_COMMIT=${ENGINE_SOURCE_COMMIT}

COPY backend/rating_engine/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/rating_engine ./rating_engine

EXPOSE 8080

# Private in a deployment: Cloud Run IAM (no anonymous invocation) is applied at
# deploy time by infrastructure/deploy_candidate_enhanced.sh, not by this file.
CMD exec uvicorn rating_engine.main:app --host 0.0.0.0 --port ${PORT:-8080}
