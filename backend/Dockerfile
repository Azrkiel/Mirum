# RC1 release image recipe. Build from the repository root:
#   docker build -f backend/Dockerfile --target release -t mirum-backend:release .
# Stages: deps (OS packages + hashed Python deps, no code) -> release (code, regen builder, non-root)
#         -> b13-eval (release + eval scripts; not wired into b13-live.yml in RC1).
# Base digest = the python:3.10-slim-bookworm image B13 run 5 resolved (Python 3.10.22);
# CI asserts the interpreter version. The pre-RC1 recipe is kept beside this file as a baseline.

FROM python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370 AS deps
# Versions are the bookworm builds current on 2026-11-18. Transitive apt dependencies
# are resolved by apt at build time (not individually pinned).
RUN apt-get update && apt-get install -y --no-install-recommends     libgl1=1.6.0-1 libglu1-mesa=9.0.2-1.1 libxrender1=1:0.9.10-1.1 libsm6=2:1.2.3-1     libxext6=2:1.3.4-1+b1 libgomp1=12.2.0-14+deb12u1 xfoil=6.99.dfsg+1-3+b1     libosmesa6=22.3.6-1+deb12u2 libgles2=1.6.0-1     && rm -rf /var/lib/apt/lists/*
COPY requirements-release.lock /tmp/requirements-release.lock
# Binary-only: an sdist anywhere fails the build closed. The one exception is PyOpenGL 3.1.0,
# which PyPI publishes only as a (hash-verified) sdist; it is built with the image's own
# setuptools/wheel and no build isolation, so no unhashed build requirement is fetched.
RUN python -c 'import setuptools, wheel' && python -m pip install --no-cache-dir --only-binary=:all: --no-binary=PyOpenGL --no-build-isolation --require-hashes --no-deps -r /tmp/requirements-release.lock && python -m pip check
RUN groupadd --gid 10001 caduser && useradd --uid 10001 --gid 10001     --no-create-home --home-dir /tmp --shell /usr/sbin/nologin caduser     && mkdir -p /tmp/mirum/engineering && chown -R 10001:10001 /tmp/mirum
WORKDIR /tmp
# Defaults below also serve docker-compose.yml (development): ./backend is bind-mounted at
# /app and ./analysis at /opt/analysis. The release stage overrides the layout-specific parts.
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app:/opt     MIRUM_WORK_DIR=/tmp/mirum/work MIRUM_TELEMETRY_DIR=/tmp/mirum/telemetry     MIRUM_ENGINEERING_DATA_DIR=/tmp/mirum/engineering     OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYOPENGL_PLATFORM=osmesa     WEB_CONCURRENCY=1 MIRUM_API_REPLICAS=1
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "8000"]

# Regen payload (PicoGK runtime .so, builder, gcc libs, licenses). Release and CI build it through
# picogk-builder/docker-bake.hcl, which overrides this empty stage with the real one (named context
# "picogk-regen"). A plain `docker build` leaves it empty and the release stage's checks fail the build.
FROM scratch AS picogk-regen

FROM deps AS release
USER root
# Regen runtime libraries (bookworm builds of what the .so links against; see ldd check below).
RUN apt-get update && apt-get install -y --no-install-recommends     libtbb12=2021.8.0-2+deb12u1 libblosc1=1.21.3+ds-1 libboost-iostreams1.74.0=1.74.0+ds1-21     libsnappy1v5=1.1.9-3 libxxhash0=0.8.1-1     && rm -rf /var/lib/apt/lists/*
COPY --from=picogk-regen /regen/app /opt/regen/app
COPY --from=picogk-regen /regen/lib /opt/regen/lib
COPY --from=picogk-regen /regen/licenses /licenses
COPY picogk-builder/picogk-runtime.sha256 /opt/regen/picogk-runtime.sha256
# Fail closed: no payload, wrong runtime build, or unresolved shared libraries means no release image.
# LD_LIBRARY_PATH is deliberately not an ENV: regen_cli sets it for the builder subprocess only.
RUN test -x /opt/regen/app/builder && test -f /opt/regen/app/picogk.26.2.so     && echo "$(cut -d' ' -f1 /opt/regen/picogk-runtime.sha256)  /opt/regen/app/picogk.26.2.so" | sha256sum -c -     && chmod -R a+rX /opt/regen /licenses     && ! LD_LIBRARY_PATH=/opt/regen/lib ldd /opt/regen/app/picogk.26.2.so | tee /dev/stderr | grep -q 'not found'
COPY backend/ /app/backend/
COPY analysis/ /app/analysis/
COPY output/wing_assembly_oml_reference.json /app/output/wing_assembly_oml_reference.json
# Code is root-owned and read-only to uid 10001; scratch lives under /tmp/mirum.
RUN chmod -R a+rX /app
ENV PYTHONPATH=/app:/app/backend     MIRUM_DEPLOYMENT=release MIRUM_ENGINEERING_REQUIRE_VOLUME=1     WEB_CONCURRENCY=1 MIRUM_API_REPLICAS=1
USER 10001:10001
CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM release AS b13-eval
USER root
COPY scripts/checked_engineering_live_eval.py scripts/checked_engineering_live_diagnostic.py /app/scripts/
# The fixed corpus is the only file let through backend/Dockerfile.dockerignore from backend/tests.
RUN chmod -R a+rX /app/scripts && test -f /app/backend/tests/fixtures/engineering/ai-corpus-v1.json
WORKDIR /app
USER 10001:10001
