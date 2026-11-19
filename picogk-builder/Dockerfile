# Built through picogk-builder/docker-bake.hcl (target regen), from the repo root.
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

# Payload only: what the release image copies in. There is no runnable OS stage here; the builder runs
# in the backend release image (python:3.10-slim-bookworm), where native tests and the API smoke test run.
FROM scratch AS regen-payload
COPY --from=publish /app /regen/app
COPY --from=picogk-runtime:26.2 /picogk/picogk.26.2.so /regen/app/picogk.26.2.so
COPY --from=picogk-runtime:26.2 /picogk/picogk.26.2.so.sha256 /regen/app/picogk.26.2.so.sha256
COPY --from=picogk-runtime:26.2 /usr/local/lib64/libstdc++.so.6 /regen/lib/libstdc++.so.6
COPY --from=picogk-runtime:26.2 /usr/local/lib64/libgcc_s.so.1 /regen/lib/libgcc_s.so.1
COPY --from=picogk-runtime:26.2 /picogk/boost-lib/ /regen/lib/
COPY --from=picogk-runtime:26.2 /picogk/boost-version.txt /regen/licenses/boost-version.txt
COPY --from=picogk-runtime:26.2 /picogk/submodules.txt /regen/licenses/picogk-submodules.txt
COPY --from=picogk-runtime:26.2 /picogk/apt-packages.json /regen/licenses/picogk-runtime-apt-packages.json
COPY --from=picogk-runtime:26.2 /src/LICENSE /regen/licenses/PicoGKRuntime-LICENSE
COPY --from=picogk-runtime:26.2 /src/openvdb/LICENSE /regen/licenses/OpenVDB-LICENSE
COPY --from=picogk-runtime:26.2 /src/GLFW/LICENSE.md /regen/licenses/GLFW-LICENSE.md
COPY --from=picogk-runtime:26.2 /src/imgui/LICENSE.txt /regen/licenses/imgui-LICENSE.txt
COPY picogk-builder/THIRD_PARTY_NOTICES.md /regen/licenses/THIRD_PARTY_NOTICES.md
