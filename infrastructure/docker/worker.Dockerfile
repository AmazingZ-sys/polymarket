FROM python:3.12-slim AS wheels
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=UTC PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.4
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra live --no-cache
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=UTC PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get -o APT::Update::Error-Mode=any -o Acquire::Retries=3 update \
 && apt-get -o Acquire::Retries=3 install --only-upgrade --no-install-recommends -y libzstd1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=wheels /app/.venv /app/.venv
COPY pqcollector ./pqcollector
COPY services ./services
COPY database ./database
RUN useradd --uid 10001 --no-create-home worker && mkdir -p /data/parquet && chown worker:worker /data/parquet
USER worker
CMD ["python","-m","pqcollector"]
