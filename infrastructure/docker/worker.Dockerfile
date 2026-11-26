FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=UTC PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.6
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
COPY pqcollector ./pqcollector
COPY services ./services
COPY database ./database
RUN useradd --uid 10001 --no-create-home worker && mkdir -p /data/parquet && chown worker:worker /data/parquet
USER worker
CMD ["python","-m","pqcollector"]
