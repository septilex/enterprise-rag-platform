FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for layer caching. Default image is lightweight (CPU, no
# torch). Set INSTALL_RERANK=true to include the heavy cross-encoder deps.
ARG INSTALL_RERANK=false
COPY requirements.txt requirements-rerank.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_RERANK" = "true" ]; then \
         pip install --no-cache-dir -r requirements-rerank.txt; \
       fi

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

# Run as non-root.
RUN useradd -m appuser
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
