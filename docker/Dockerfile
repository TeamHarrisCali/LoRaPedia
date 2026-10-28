# LoRaPedia as a container image. Build from the repository root:  docker build -f docker/Dockerfile -t lorapedia .
# Compose file: docker-compose.yml. Base images are pinned by tag AND digest (multi-arch index digests, so the same
# line works on amd64 and arm64); Dependabot proposes new digests.

# ---- Stage 1: build the web UI (runs on the build machine's own architecture, so no emulation) ------------------
# buildx sets BUILDPLATFORM itself; the default only matters for the classic builder, which does not.
ARG BUILDPLATFORM=linux/amd64
FROM --platform=$BUILDPLATFORM node:22-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ----------------------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

# PYTHONDONTWRITEBYTECODE: nothing is written into the (read-only) code folder. LORAPEDIA_INSTALL=docker tells the app it is
# in a container, where it cannot update itself but can ask the separate updater container to.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LORAPEDIA_INSTALL=docker LORAPEDIA_DATA=/data LORAPEDIA_STATIC=/app/frontend/dist \
    LORAPEDIA_HOST=0.0.0.0 LORAPEDIA_PORT=8000 HOME=/tmp

WORKDIR /app
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/lorapedia ./backend/lorapedia
RUN pip install ./backend
COPY --from=ui /ui/dist ./frontend/dist
COPY LICENSE CHANGELOG.md ./

# A numeric, unprivileged user. /data (a volume) is the only place the app writes besides /tmp. A new named volume takes
# this folder's ownership the first time, so it is writable by this user; a bind-mounted host folder must be made
# writable for uid 10001 by hand. This image runs as that user by default. docker-compose.yml starts it as root instead, so the
# entrypoint can give the app the group of a USB radio's device file (whatever that is on this computer) before dropping to it.
COPY docker/entrypoint.sh /usr/local/bin/lorapedia-entrypoint
RUN chmod 755 /usr/local/bin/lorapedia-entrypoint \
 && groupadd --gid 10001 lorapedia \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin lorapedia \
 && mkdir /data && chown 10001:10001 /data
USER 10001:10001
VOLUME /data
EXPOSE 8000

# Release builds set these so the running container can say which release it is (the updater reads the label).
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="LoRaPedia" \
      org.opencontainers.image.description="Offline Wikipedia Q&A for Meshtastic" \
      org.opencontainers.image.source="https://github.com/TeamHarrisCali/LoRaPedia" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).read()"]

ENTRYPOINT ["lorapedia-entrypoint"]
CMD ["lorapedia"]
