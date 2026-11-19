# Frontend release image. Build from the repository root:
#   docker build -f frontend/Dockerfile -t mirum-frontend:release .
FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS explorer-build
WORKDIR /build
COPY frontend/components/glider_explorer/package*.json ./
RUN npm ci
COPY frontend/components/glider_explorer/ ./
RUN npm run build

# Babylon.js is vendored from the lockfile and served locally (no CDN at runtime).
FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS viewer-assets
WORKDIR /build
COPY frontend/viewer_assets/package.json frontend/viewer_assets/package-lock.json ./
RUN npm ci --ignore-scripts
RUN mkdir -p /out/babylon     && cp node_modules/babylonjs/babylon.js           node_modules/babylonjs-loaders/babylonjs.loaders.min.js           node_modules/babylonjs-materials/babylonjs.materials.min.js /out/babylon/

FROM python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370
COPY frontend/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes --no-deps -r /tmp/requirements.lock     && python -m pip check
RUN groupadd --gid 10001 appuser && useradd --uid 10001 --gid 10001     --no-create-home --home-dir /tmp --shell /usr/sbin/nologin appuser

COPY frontend/ /app/
COPY backend/dragsters/ /app/backend/dragsters/
COPY --from=explorer-build /build/dist /app/components/glider_explorer/dist
COPY --from=viewer-assets /out/babylon/ /app/static/babylon/
RUN chmod -R a+rX /app
WORKDIR /app
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
