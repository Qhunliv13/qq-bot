# Build the dashboard from the exact embedded upstream source, not a remote UI.
FROM node:20-bookworm-slim AS webui
WORKDIR /src/dashboard
COPY vendor/astrbot/dashboard/ ./
# Vite emits this pinned sibling asset into the dashboard's t2i directory.
COPY vendor/astrbot/astrbot/core/utils/t2i/template/shiki_runtime.iife.js /src/astrbot/core/utils/t2i/template/shiki_runtime.iife.js
RUN corepack enable && corepack prepare pnpm@10.11.0 --activate \
    && pnpm install --frozen-lockfile \
    && pnpm run build

FROM python:3.12-slim-bookworm AS bot
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ASTRBOT_BUILD_DASHBOARD=0 \
    HIGAN_ROOT=/root/higan \
    ASTRBOT_ROOT=/AstrBot \
    HIGAN_SETTINGS_FILE=/run/qhunliv/settings.json
WORKDIR /AstrBot
# Keep Node available to the upstream runtime without curl-to-shell installers.
COPY --from=webui /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev libssl-dev ca-certificates \
    ffmpeg libavcodec-extra fonts-noto-cjk git ripgrep \
    && rm -rf /var/lib/apt/lists/*
# This preserves the upstream LICENSE, EULA, notices and full source in the image.
COPY vendor/astrbot/ /AstrBot/
COPY --from=webui /src/dashboard/dist/ /AstrBot/astrbot/dashboard/dist/
# Upstream ships pyproject.toml but no Python dependency lock at this tag.
# Install its declared runtime requirements without modifying the vendored tree.
RUN python -m pip install . \
    && python -c "import pathlib,tomllib; p=pathlib.Path('/AstrBot'); v=tomllib.loads((p/'pyproject.toml').read_text())['project']['version']; d=p/'astrbot/dashboard/dist/assets'; d.mkdir(parents=True,exist_ok=True); (d/'version').write_text(v,encoding='utf-8')"
COPY plugin/ /opt/qhunliv/plugin/
COPY tools/local_runtime_client.py /opt/qhunliv/local_runtime_client.py
COPY deploy/entrypoint.py /opt/qhunliv/entrypoint.py
COPY deploy/healthcheck.py /opt/qhunliv/healthcheck.py
COPY LICENSE THIRD_PARTY_NOTICES.md vendor/ASTRBOT_UPSTREAM.json /opt/qhunliv/
EXPOSE 6185 6199
# Health checks plugin intake and local dashboard HTML, not models or tool dependencies.
HEALTHCHECK --interval=10s --timeout=6s --start-period=180s --retries=6 \
    CMD ["python", "/opt/qhunliv/healthcheck.py"]
ENTRYPOINT ["python", "/opt/qhunliv/entrypoint.py"]
