FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY pyproject.toml README.md ./
COPY --chown=app:app podcast_generator ./podcast_generator
COPY --chown=app:app input/podcast_preferences.json ./input/podcast_preferences.json

RUN pip install --no-cache-dir . \
    && mkdir -p /app/output \
    && chown -R app:app /app

USER app

CMD ["sh", "-c", "uvicorn podcast_generator.api.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
