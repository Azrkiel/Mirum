# Fly.io backend: the same pinned CadQuery/OCP environment as Dockerfile.verify
# (the offline native gate), plus the FastAPI server. Build from the repo root.
FROM python:3.10-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglu1-mesa libxrender1 libsm6 libxext6 libgomp1 xfoil libosmesa6 libgles2 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-linux.lock /tmp/requirements-linux.lock
RUN python -m pip install --no-cache-dir -r /tmp/requirements-linux.lock
COPY requirements-mirum-verification.lock /tmp/mirum-verification.lock
RUN python -m pip install --no-cache-dir -r /tmp/mirum-verification.lock \
    && python -m pip check
RUN groupadd --gid 10001 caduser && useradd --uid 10001 --gid 10001 \
    --no-create-home --home-dir /tmp --shell /usr/sbin/nologin caduser
COPY backend/ /app/backend/
COPY analysis/ /app/analysis/
COPY output/wing_assembly_oml_reference.json /app/output/wing_assembly_oml_reference.json
WORKDIR /tmp
# Code is root-owned and read-only to caduser; scratch lives under /tmp/mirum.
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app:/app/backend \
    MIRUM_WORK_DIR=/tmp/mirum/work MIRUM_TELEMETRY_DIR=/tmp/mirum/telemetry \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYOPENGL_PLATFORM=osmesa
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000"]
