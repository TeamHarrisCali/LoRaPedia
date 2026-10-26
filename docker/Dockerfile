# Build from the repository root:  docker build -f docker/Dockerfile -t lorapedia .

# ---- Stage 1: build the web UI -------------------------------------------------------------
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ----------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    LORAPEDIA_DATA=/data LORAPEDIA_STATIC=/app/frontend/dist LORAPEDIA_HOST=0.0.0.0 LORAPEDIA_PORT=8000

WORKDIR /app
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/lorapedia ./backend/lorapedia
RUN pip install ./backend
COPY --from=ui /ui/dist ./frontend/dist

# Run as a normal user. Serial devices are reached through group_add in docker-compose.yml.
RUN useradd --create-home --uid 1000 lorapedia && mkdir /data && chown lorapedia /data
USER lorapedia
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["lorapedia"]
