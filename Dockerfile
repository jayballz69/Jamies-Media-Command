FROM python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CM_DATA_DIR=/data CM_HOST=0.0.0.0 CM_PORT=8780
WORKDIR /app
COPY requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt \
    && groupadd --gid 1000 collection \
    && useradd --uid 1000 --gid 1000 --no-create-home collection \
    && mkdir /data && chown collection:collection /data
COPY collection_web ./collection_web
USER 1000:1000
EXPOSE 8780
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8780/healthz', timeout=3)"
CMD ["python", "-m", "collection_web"]
