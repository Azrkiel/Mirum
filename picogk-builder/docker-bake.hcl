# Build the PicoGK runtime and the regen builder image in one graph (no registry, no daemon image
# needed between them). Run from the repo root:  docker buildx bake -f picogk-builder/docker-bake.hcl regen
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
  contexts   = { "picogk-runtime:26.2" = "target:runtime" }
  tags       = ["picogk-regen:slice"]
}

# Runtime artifacts only (.so, sha256, ldd, submodule status, apt manifest), exported as files.
target "runtime-files" {
  contexts          = { runtime = "target:runtime" }
  dockerfile-inline = "FROM scratch\nCOPY --from=runtime /picogk /picogk\n"
}
