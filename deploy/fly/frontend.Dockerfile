FROM node:22-bookworm-slim AS explorer-build
WORKDIR /build
COPY frontend/components/glider_explorer/package*.json ./
RUN npm ci
COPY frontend/components/glider_explorer/ ./
RUN npm run build

# Fly.io Streamlit frontend. Build from the repo root.
FROM python:3.10-slim
COPY frontend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN useradd --uid 10001 --home-dir /tmp --shell /usr/sbin/nologin webuser
COPY frontend/ /app/
COPY backend/dragsters/ /app/backend/dragsters/
COPY --from=explorer-build /build/dist /app/components/glider_explorer/dist
RUN chmod -R a+rX /app
WORKDIR /app
ENV HOME=/tmp
USER 10001
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
