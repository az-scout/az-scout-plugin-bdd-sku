# ---------- build ----------
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq-dev gcc \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

# ---------- runtime ----------
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /build/src/sku_mapper_job /app/sku_mapper_job

RUN useradd -m -u 1000 appuser \
 && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "sku_mapper_job"]
