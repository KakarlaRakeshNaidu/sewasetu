FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377
WORKDIR /app
RUN groupadd --gid 10001 portal && useradd --uid 10001 --gid portal --no-create-home portal \
    && mkdir -p /var/sewasetu/gateway && chown 10001:10001 /var/sewasetu/gateway
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 120 --retries 3 -r requirements.txt && pip check
COPY app.py .
USER 10001:10001
EXPOSE 8025
HEALTHCHECK --interval=20s --timeout=4s --retries=3 CMD ["python","-c","import urllib.request; urllib.request.urlopen('http://127.0.0.1:8025/health',timeout=3)"]
CMD ["gunicorn","--bind","0.0.0.0:8025","--workers","1","--threads","4","--timeout","30","app:app"]
