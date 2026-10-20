# Verified nominal four-hole plate (nonproduction demonstrator)

This example proves one useful pattern for Mirum: **typed numerical requirements → deterministic B-rep → measurements of actual geometry → STEP reimport checks**. It is not wired into the application and is not engineering certification, manufacturing GD&T verification or a general CAD validator.

## Files

- `plate.py`: immutable bounded specification, deterministic CadQuery builder, independent surface/size checks and checked STEP export.
- `test_plate.py`: real-kernel adversarial tests; no missing-kernel skips or model inference.
- `probe_existing_gates.py`: actual published/local certificate probes. The separately labelled published adapter probe uses real OCP operations to isolate old checker semantics from its missing binding.
- `render_preview.py`: visualization of the actual reimported STEP. A preview is not geometric evidence.

## Scope

Millimetres; fixed XYZ frame; axis-aligned sharp rectangular plate, bottom Z=0; four identical cylindrical Z-through-bores on a symmetric rectangular pattern. Unsupported/decomposed surface representations can be conservatively rejected even if geometrically equivalent. Scalar diameter/size tolerances, radial hole-location budget and kernel-tolerance ceiling are explicitly defined; they are not ASME/ISO datum-zone semantics or physical metrology uncertainties.

Default specimen: 80 × 50 × 6 mm; four Ø6 mm holes, pitch 60 × 30 mm. No loads, material allowables, manufacturing process or physical inspection are verified. Construction and checking share the OCCT kernel; this is not independent-kernel proof. Do not use it as a hostile STEP-file service.

## Native tests

The audit used an **existing local** Docker image with CadQuery 2.6.1/OCP 7.8.1.1. That image tag is not a published downloadable distribution. On a prepared Linux CadQuery/OCP + pytest environment:

```sh
python -m pytest examples/verified_plate/test_plate.py -q -p no:cacheprovider
python examples/verified_plate/plate.py artifacts/my-new-run
python examples/verified_plate/render_preview.py artifacts/my-new-run
```

The output directory must not already exist. STEP and evidence are staged together, then published by one same-filesystem directory rename. Handled validation/publication failure leaves no accepted output directory. This is a single-owner demonstration, not power-loss durability or a concurrent artifact store. A successful run emits `plate.step` and `validation.json`. Preview rendering also needs NumPy/Pillow and uses a software depth buffer; it has no OpenGL dependency.

To reproduce with the existing local Docker runtime from Windows Git Bash:

```sh
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --init \
  --cap-drop ALL --security-opt no-new-privileges --memory 2g --cpus 2 \
  --tmpfs /tmp:rw,size=256m,mode=1777 \
  --mount type=bind,source=C:/Users/jiang/Mirum-brep-audit,target=/audit,readonly \
  -w /tmp mirum-integration-verify:latest \
  python -m pytest /audit/examples/verified_plate/test_plate.py \
  -q -p no:cacheprovider --tb=short
```

Export needs a separate writable artifacts bind mount; source remains read-only. Provider calls are unnecessary. Evidence contains container-internal paths plus hashes; host artifacts live under the corresponding bind-mounted folder.

## Acceptance is deliberately narrow

Checks include pure one-solid body accounting, closure/BRepCheck, finite volume, actual size limits and exterior datum planes, cylindrical-hole count/diameter/position/axis/extent, kernel tolerances and same-kernel STEP round-trip Boolean difference plus independent mass and measured-feature comparisons. Zero Boolean difference alone does not prove exact equivalence. Kernel/algorithm budgets do not promise as-manufactured precision. Hashes detect mismatched bytes but are not signatures.

See `../../docs/brep-engineering-audit.md` and `../../docs/brep-verification-results.md` for architecture recommendations, final executed evidence, review scope and limitations. The live Mirum source and prior dirty integration checkout remain unchanged.
