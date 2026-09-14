FROM node:20-bookworm-slim AS web-builder

WORKDIR /build/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    CHAOXING_DATA_DIR=/app/data \
    CHAOXING_RUNNING_IN_DOCKER=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Keep the non-secret configuration template available to CLI users without
# copying a host config or any credentials into the image.
COPY config.ini.example ./config.ini.example
COPY . ./
COPY --from=web-builder /build/web/dist ./web/dist

RUN mkdir -p /app/data

VOLUME ["/app/data"]
EXPOSE 5000

CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:5000", "app:app"]
