# Build from the repo root:  docker build -f picogk-builder/Dockerfile -t picogk-regen:slice .
# Needs picogk-runtime:26.2 (spike-picogk/Dockerfile.runtime) and picogk-builder/avast-ca.pem
# (machine-local, gitignored: copy spike-picogk/avast-ca.pem; Avast intercepts TLS).
FROM mcr.microsoft.com/dotnet/sdk:9.0 AS publish
COPY picogk-builder/avast-ca.pem /usr/local/share/ca-certificates/avast-ca.crt
RUN update-ca-certificates
WORKDIR /src
COPY picogk-builder/Program.cs picogk-builder/builder.csproj ./
RUN dotnet publish builder.csproj -c Release -o /app

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      libtbb12 libblosc1 libboost-iostreams1.83.0 zlib1g libzstd1 liblz4-1 \
      libgl1 libx11-6 \
      python3 python3-numpy python3-scipy python3-pydantic \
    && rm -rf /var/lib/apt/lists/*
COPY --from=picogk-runtime:26.2 /usr/local/lib64/libstdc++.so.6 /usr/local/lib/picogk-gcc/libstdc++.so.6
COPY --from=picogk-runtime:26.2 /usr/local/lib64/libgcc_s.so.1 /usr/local/lib/picogk-gcc/libgcc_s.so.1
COPY --from=publish /app /app
COPY --from=picogk-runtime:26.2 /picogk/picogk.26.2.so /app/picogk.26.2.so
COPY --from=picogk-runtime:26.2 /picogk/picogk.26.2.so.sha256 /app/picogk.26.2.so.sha256
COPY --from=picogk-runtime:26.2 /src/LICENSE /licenses/PicoGKRuntime-LICENSE
COPY --from=picogk-runtime:26.2 /src/openvdb/LICENSE /licenses/OpenVDB-LICENSE
COPY --from=picogk-runtime:26.2 /src/GLFW/LICENSE.md /licenses/GLFW-LICENSE.md
COPY --from=picogk-runtime:26.2 /src/imgui/LICENSE.txt /licenses/imgui-LICENSE.txt
COPY picogk-builder/THIRD_PARTY_NOTICES.md /licenses/THIRD_PARTY_NOTICES.md
# Only the regen files. backend/engineering/__init__.py is a docstring, so no side effects.
COPY backend/engineering/__init__.py backend/engineering/regen_spec.py backend/engineering/regen_geometry.py backend/engineering/regen_mesh.py backend/engineering/regen_cli.py /py/engineering/
# LD_LIBRARY_PATH is NOT set globally (it could break scipy); regen_cli sets it for the builder subprocess only.
ENV REGEN_BUILDER=/app/builder REGEN_GCC_LIB=/usr/local/lib/picogk-gcc PYTHONPATH=/py PYTHONDONTWRITEBYTECODE=1
LABEL org.opencontainers.image.source="https://github.com/Azrkiel/Mirum" \
      org.opencontainers.image.description="Mirum regen chamber generator test build. Geometry checks only; do not manufacture or fire hardware based on its output."
WORKDIR /out
ENTRYPOINT ["python3", "-m", "engineering.regen_cli"]
