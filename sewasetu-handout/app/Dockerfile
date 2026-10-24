FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377
RUN apt-get update && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Kolkata PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN ln -snf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime && echo Asia/Kolkata > /etc/timezone \
    && groupadd --gid 10001 portal && useradd --uid 10001 --gid portal --no-create-home portal
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 120 --retries 3 -r requirements.txt && pip check
COPY *.py schema.sql ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY deploy ./deploy
COPY templates ./templates
COPY static ./static
ARG RELEASE_SHA=development
RUN printf '%s\n' "$RELEASE_SHA" > /app/RELEASE \
    && mkdir -p /var/sewasetu/uploads /var/log/sewasetu \
    && chown 10001:10001 /var/sewasetu/uploads /var/log/sewasetu \
    && crontab /app/deploy/crontab
LABEL org.opencontainers.image.revision=$RELEASE_SHA
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 CMD ["python","-c","import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=4)"]
CMD ["gunicorn","--bind","0.0.0.0:8000","--workers","4","--timeout","30","--graceful-timeout","30","--limit-request-line","4094","--limit-request-field_size","8190","app:app"]
