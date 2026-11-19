# Mirum Engineering Language (MEL) v0.1 proposal

This is an implementation specification, not an already working language. Extension: .mir. F01 freezes the design; F04/F05 implement it. MIRUM's language owns engineering meaning while targeting existing kernels.

## Initial grammar

One file declares one language version and one design. The first version supports named assignments, strings, finite numbers with optional units, and lists. No general expressions or user functions are necessary for the first complete vertical slice.

```ebnf
program    = "language", "mirum", string, ";",
             "design", identifier, "{", { assignment }, "}" ;
assignment = identifier, "=", value, ";" ;
value      = quantity | string | list ;
quantity   = number, [ identifier ] ;
list       = "[", [ value, { ",", value } ], "]" ;
identifier = letter_or_underscore, { letter_or_digit_or_underscore } ;
number     = [ "-" ], digits, [ ".", digits ],
             [ ( "e" | "E" ), [ "+" | "-" ], digits ] ;
string     = JSON_quoted_string ;
```

UTF-8 input; whitespace and // line comments are ignored outside strings. Keywords are reserved. F04 must spell out lexical character classes and error handling, including invalid Unicode/control characters and malformed escapes. Duplicate assignment keys and unknown language versions fail. Formatter preserves meaning and has a stable style.

## Beam fixture

See [beam.mir](examples/beam.mir). Field names below are the proposed language surface, translated to the registered rectangular_cantilever 1.0.0 capability rather than blindly forwarded to Python.

Required fields: capability, capability_version, backend, numerical_policy, resource_policy, length, width, dimensional_tolerance, tip_force, max_tip_deflection, youngs_modulus, allowable_stress, material_reference, material_source_kind, assumptions, mode, candidate_heights and exports. F01 verifies material_source_kind mapping against actual B03 schema and freezes an explicit translation; no unknown value is silently defaulted.

The backend symbolic selector occt resolves to a pinned installed backend before review. It is not “choose whatever works.” source fields contain explicit policy versions. Exports must be supported by the chosen adapter and capability. Other capabilities use their own registered typed field sets; unsupported fields fail, rather than warn and continue.

## Type and semantic checks

- Length quantities convert among supported mm/cm/m/in; force N/kN; pressure/modulus Pa/MPa/GPa. Require dimensional compatibility, finite normalized results and capability ranges.
- Unitless physical quantities fail unless the registered type is explicitly dimensionless.
- Kg is mass; no automatic mass-to-force conversion in v0.1 source. Existing reviewed conversion workflows can produce an explicit force before compilation.
- No arbitrary equations, implicit solver inference or invented material tables. Physics methods are registered/versioned algorithms.
- Candidate lists obey the reviewed family policy; for beam, at most four unique values in the supported envelope.
- Unknown assignments, capability/backend versions, incompatible exports, missing assumptions and unresolved references are compilation errors.
- Material/source references are assertions to review, not evidence that physical stock is certified.
- Compile and plan can run offline; actual native backend readiness is checked before execution. Live AI is optional.

## Compiler stages and executable output

1. Lex/parse into AST with source ranges.
2. Resolve field schema using the exact capability version.
3. Normalize quantities; type-check and check model envelope.
4. Create canonical ProgramIR and immutable requirement/check plan.
5. Negotiate backend/export compatibility, resolve ExecutionLock and show a readable plan.
6. Finalize the exact reviewed semantics using the existing review service.
7. Lower the supported capability plan to a registered adapter and run existing evidence/acceptance policy.

Each failure has code/category, source span, field/operation/requirement identifier and actionable hint. No native mutation before successful review and admission.

AST-to-IR lowering is deterministic for the same supported versions. An execution lock changes when backend/native/solver/numerical policies change. A source-only formatting edit can retain semantic approval only after recomputing and proving semantic equality; provenance still records the changed source.

## Tests and future syntax

Parser/formatter round trips, whitespace/comment equivalence, lexical failures, duplicate keys, unit confusion, nonfinite overflow, invalid versions, budget exhaustion and hostile Python-like input are mandatory. Equivalent .mir and form briefs must satisfy P03/P07.

Later versions may add reusable components, dimensional expressions, assemblies or graphs only after their semantics, budgets, feature identity and evidence obligations are specified. Version migration returns an explicit semantic diff and requires renewed review where meaning changes. Never reinterpret old programs silently.
