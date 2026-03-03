FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install only runtime deps needed by the standalone API
# (az-scout is NOT required – it's only needed for plugin registration)
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code – no pip install needed, PYTHONPATH handles imports
COPY src/ ./src/
COPY api/ ./api/

ENV PYTHONPATH=/app/src

# Non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
