# Build the PicoGK runtime, the regen payload and the release backend image in one graph (no registry).
# From the repo root:  docker buildx bake -f picogk-builder/docker-bake.hcl backend-release --load
# Local TLS-intercepting proxy: add  --set '*.secrets=id=extra_ca,src=<pem>'  (see docs/implementation/regen-chamber-slice.md).
variable "BUILD_JOBS" {
  default = "3"
}

target "runtime" {
  context    = "spike-picogk"
  dockerfile = "Dockerfile.runtime"
  args       = { BUILD_JOBS = BUILD_JOBS }
  tags       = ["picogk-runtime:26.2"]
}

target "regen" {
  context    = "."
  dockerfile = "picogk-builder/Dockerfile"
  target     = "regen-payload"
  contexts   = { "picogk-runtime:26.2" = "target:runtime" }
}

# The release backend image: the empty "picogk-regen" stage in backend/Dockerfile is overridden by the payload.
target "backend-release" {
  context    = "."
  dockerfile = "backend/Dockerfile"
  target     = "release"
  contexts   = { picogk-regen = "target:regen" }
  tags       = ["mirum-backend:release"]
  # Fresh OS packages and Python wheels every build (the old --no-cache), while the PicoGK stages stay cached.
  no-cache-filter = ["deps", "release"]
}

target "backend-b13-eval" {
  inherits = ["backend-release"]
  target   = "b13-eval"
  tags     = ["mirum-backend:b13-eval"]
}

# Runtime artifacts only (.so, sha256, ldd, submodule status, apt manifest), exported as files.
target "runtime-files" {
  contexts          = { runtime = "target:runtime" }
  dockerfile-inline = "FROM scratch\nCOPY --from=runtime /picogk /picogk\n"
}
