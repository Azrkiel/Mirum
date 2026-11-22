# MEL-S negative corpus

These hand-written cases come from the F05 semantic diagnostic contract. Each
starts with the valid `../../p03/beam.mir` fixture. The corpus pins the exact
MEL diagnostic code, legacy IR code, category, field, and source line. The
runner also checks each diagnostic's hint against `HINTS`.

`cases.json` contains `base`, a path relative to this directory, and a `cases`
array. Each case has a unique `id`, an ordered `edits` array of `[old, new]`
pairs, and the expected `code`, `ir_code`, `category`, `field`, and one-based
`line`. Each `old` must be present when its edit runs. The runner replaces its
first occurrence. JSON `"\\u202e"` represents the six ASCII characters of the
MEL string escape. The MEL lexer decodes the escape before semantic checks.

The runner tests `MEL-S016` separately because `source_id` is a compiler
argument. The corpus follows the F05 coverage list. It leaves out literal
overflow (`MEL-L012`), internal diagnostics (`MEL-S017` and `MEL-S019`), and
the resource deadline (`MEL-S018`).

## Diagnostic precedence

- `unknown-capability-before-unknown-field` fails in `_resolve_schema` before
  `_normalize` examines other assignment names. The unsupported capability
  cases use the `capability` assignment span. The unsupported version cases
  have a known capability and use the `capability_version` assignment span.
- `unknown-beam-width-before-missing-width` makes `beam_width` unknown and
  leaves `width` missing. The unknown-name scan runs first.
- `missing-width-before-invalid-design-name` reports the missing `width` at
  the design block start because required fields precede design-name checks.
- `missing-material-source-kind` gets `MEL-S001` from the required-field
  check at the design block start. `material-source-kind-number` gets
  `MEL-S001` from the field's kind check before the generic string check.
- `invalid-backend-before-bad-length-unit` follows `FIELDS` order. Backend
  support is checked before length units, even though the length edit is
  applied first.
- `multiline-quantity-export-item` fails string conversion before the export
  list comparison. It reports the item's line, which follows the assignment
  line. `candidate-item-unit-before-list-count` likewise reports a unitless
  fifth item before checking the list count.
- `duplicate-candidate-heights-after-conversion` reaches uniqueness after
  converting `0.5 cm` to `5 mm`. The compiler rejects the duplicate before
  the reviewed brief validator can collapse it.
- `assumptions-escaped-u202e`, `assumptions-escaped-u0007`,
  `material-reference-escaped-u200b`, and
  `multiline-material-reference-escaped-u2028` fail in `_string` before
  reviewed brief validation. The multiline case points to the string's
  starting line. `multiline-string-for-quantity` instead points to the
  assignment start because the scalar kind check uses the assignment span.
- `negative-candidate-height`, `zero-candidate-height`, and
  `candidate-height-outside-envelope` pass quantity conversion, count, and
  uniqueness checks. The reviewed brief validator reports `height_mm`, which
  maps back to `candidate_heights` and `MEL-S014` at the assignment start.
  `empty-exports-before-negative-candidate` fails the export check before
  brief validation reaches the negative height.
- `width-outside-envelope` passes unit conversion. The brief's square-section
  envelope check reports `requirements.width_mm` first, ahead of the derived
  `requirements.height_mm` issue. The compiler maps that first issue to
  `width` and `MEL-S015` at the width assignment start.
