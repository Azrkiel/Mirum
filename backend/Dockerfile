FROM continuumio/miniconda3

# libGL.so.1 is a runtime dependency of OCP (the OpenCascade binding that
# CadQuery sits on). Recent conda-forge cadquery builds ship libGLU /
# libGLEW but no longer bundle libGL itself, so rebuilds started failing
# with "libGL.so.1: cannot open shared object file". Install it from apt.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgl1 && \
    rm -rf /var/lib/apt/lists/*

# Create conda environment with Python 3.10 and CadQuery from conda-forge
RUN conda create -n cadenv python=3.10 -y && \
    conda install -n cadenv -c conda-forge cadquery -y && \
    conda clean -afy

# Install Python dependencies inside the conda environment
COPY requirements.txt /tmp/requirements.txt
RUN /opt/conda/envs/cadenv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# /app is a read-only bind-mount owned by the host user at runtime.
# Using /tmp as WORKDIR means any subprocess that calls os.chdir(cwd) lands
# in writable /tmp instead of the root-owned /app, avoiding PermissionError.
# PYTHONPATH=/app lets Python find main.py and all other app modules.
WORKDIR /tmp

# Home must also be writable — set to /tmp (backed by tmpfs in compose).
RUN groupadd -r caduser && \
    useradd -r -g caduser -d /tmp -s /bin/bash caduser

EXPOSE 8000

ENV HOME=/tmp \
    XDG_CONFIG_HOME=/tmp/.config \
    CONDA_NO_PLUGINS=true \
    PYTHONPATH=/app

USER caduser

# No --reload: reload spawns a worker subprocess via multiprocessing.spawn,
# which calls os.chdir('/app') in the child — that fails because /app is a
# read-only bind-mount owned by the host user (not caduser).
# Hot-reload is also unnecessary since deploy.ps1 does `docker compose restart`.
#
# --workers 1 (intentional, plan §4 A-07): the v1 JobRegistry is per-process
# in-memory (backend/jobs.py). Submit-and-poll across workers would silently
# drop jobs. Multi-worker requires sticky session or Redis; we'll prove the
# /jobs unblocking helps before adding that complexity.
#
# --timeout-keep-alive 600 (10 min) replaces the default 5s. The /jobs SSE
# replay endpoint streams events for the duration of a long generation, and
# the default keep-alive would tear down a quiet connection (no events for
# >5s during a slow Planner phase) before the client could reconnect with
# a cursor. Frontend /jobs poll cycle is 2s, well under this bound.
CMD ["/opt/conda/envs/cadenv/bin/uvicorn", "main:app", \
     "--app-dir", "/app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "600"]
