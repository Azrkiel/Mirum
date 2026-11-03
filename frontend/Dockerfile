FROM node:22-bookworm-slim AS explorer-build
WORKDIR /build
COPY components/glider_explorer/package*.json ./
RUN npm ci
COPY components/glider_explorer/ ./
RUN npm run build

FROM python:3.10-slim

# Install pinned dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app/
COPY --from=explorer-build /build/dist /app/components/glider_explorer/dist
WORKDIR /app

EXPOSE 8501

# Cleaned up the redundant flags
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]