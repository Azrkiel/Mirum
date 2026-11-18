# Build from the repo root:  docker build -f picogk-builder/Dockerfile -t picogk-regen:slice .
# Needs picogk-runtime:26.2: a LOCAL tag from spike-picogk/Dockerfile.runtime (built in
# .github/workflows/picogk-runtime.yml before this file; never pulled from a registry).
# Optional TLS-intercepting-proxy CA (never required): add
#   --secret id=extra_ca,src=<path to pem>
# It is only used inside the RUN that needs it and is not stored in any layer.
FROM mcr.microsoft.com/dotnet/sdk:9.0@sha256:1dfdb27e9c2c345cb598925ccf6b13719ecfebea8467bd4745c23a3c108cdf84 AS publish
WORKDIR /src
COPY picogk-builder/Program.cs picogk-builder/builder.csproj picogk-builder/packages.lock.json picogk-builder/nuget.config ./
RUN \
    --mount=type=secret,id=extra_ca,required=false \
    set -eu; \
    CAFILE=/etc/ssl/certs/ca-certificates.crt; \
    if [ -s /run/secrets/extra_ca ]; then \
      cat "$CAFILE" /run/secrets/extra_ca > /tmp/ca-bundle.crt; \
      CAFILE=/tmp/ca-bundle.crt; \
      export SSL_CERT_FILE="$CAFILE" GIT_SSL_CAINFO="$CAFILE" CURL_CA_BUNDLE="$CAFILE"; \
    fi; \
    dotnet restore builder.csproj --locked-mode \
 && dotnet publish builder.csproj -c Release -o /app --no-restore \
 && rm -f /tmp/ca-bundle.crt

FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
# Same optional-CA handling as the runtime image (apt may be reached through a TLS-intercepting proxy).
RUN \
    --mount=type=secret,id=extra_ca,required=false \
    set -eu; \
    CAFILE=/etc/ssl/certs/ca-certificates.crt; \
    if [ -s /run/secrets/extra_ca ]; then \
      cat "$CAFILE" /run/secrets/extra_ca > /tmp/ca-bundle.crt; \
      CAFILE=/tmp/ca-bundle.crt; \
      export SSL_CERT_FILE="$CAFILE" GIT_SSL_CAINFO="$CAFILE" CURL_CA_BUNDLE="$CAFILE"; \
    fi; \
    apt-get -o Acquire::https::CaInfo="$CAFILE" update \
 && apt-get -o Acquire::https::CaInfo="$CAFILE" install -y --no-install-recommends \
      libtbb12 libblosc1 libboost-iostreams1.83.0 zlib1g libzstd1 liblz4-1 \
      libgl1 libx11-6 \
      python3 python3-numpy python3-scipy python3-pydantic \
    && mkdir -p /licenses \
    && dpkg-query -W -f '{"package":"${Package}","version":"${Version}"}\n' | sed '$!s/$/,/' | { echo '['; cat; echo ']'; } > /licenses/apt-packages.json \
    && rm -f /tmp/ca-bundle.crt && rm -rf /var/lib/apt/lists/*
COPY --from=picogk-runtime:26.2 /usr/local/lib64/libstdc++.so.6 /usr/local/lib/picogk-gcc/libstdc++.so.6
COPY --from=picogk-runtime:26.2 /usr/local/lib64/libgcc_s.so.1 /usr/local/lib/picogk-gcc/libgcc_s.so.1
COPY --from=publish /app /app
COPY --from=picogk-runtime:26.2 /picogk/picogk.26.2.so /app/picogk.26.2.so
COPY --from=picogk-runtime:26.2 /picogk/picogk.26.2.so.sha256 /app/picogk.26.2.so.sha256
COPY --from=picogk-runtime:26.2 /picogk/submodules.txt /licenses/picogk-submodules.txt
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
