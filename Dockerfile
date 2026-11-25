FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NETRIAGE_DB=/data/netriage.db

WORKDIR /app

COPY pyproject.toml README.md ./
COPY netriage ./netriage
RUN pip install --no-cache-dir .

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)"

CMD ["netriage", "serve", "--host", "0.0.0.0", "--port", "8080", "--db", "/data/netriage.db"]
