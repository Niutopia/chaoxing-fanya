FROM node:20-bookworm-slim AS web-builder

WORKDIR /build/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHAOXING_DATA_DIR=/app/data \
    CHAOXING_RUNNING_IN_DOCKER=1

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

# The application only writes runtime state and logs below /app/data.  Keep
# source files read-only to the unprivileged process and let Compose provide a
# writable named volume for this directory.
RUN groupadd --system --gid 10001 chaoxing \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home chaoxing \
    && install --directory --owner=chaoxing --group=chaoxing --mode=0700 /app/data /app/logs

# Keep the non-secret configuration template available to CLI users without
# copying a host config or any credentials into the image.
COPY --chown=chaoxing:chaoxing config.ini.example ./config.ini.example
COPY --chown=chaoxing:chaoxing . ./
COPY --from=web-builder --chown=chaoxing:chaoxing /build/web/dist ./web/dist

VOLUME ["/app/data"]
EXPOSE 5000

USER chaoxing:chaoxing

CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:5000", "app:app"]
