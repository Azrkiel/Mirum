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

UTF-8 input; whitespace and // line comments are ignored outside strings. Keywords are reserved. F04 must spell out lexical character classes and error handling, including invalid Unicode/control characters and malformed escapes. Duplicate assignment keys and unknown language versions fail. Formatter preserves meaning and has a stable style. (Lexical details are frozen in F01 below.)

## Lexical specification (Frozen in F01)

Frozen in F01. These rules refine the EBNF above; they do not change the grammar. The lexer is deterministic and ASCII-only outside strings and comments.

### 1. Encoding and size

- Input is UTF-8. Invalid UTF-8 (overlong forms, encoded lone surrogates, truncated sequences) is an error.
- A leading byte-order mark (EF BB BF) is rejected. A NUL byte anywhere is an error.
- Source budget is 64 KiB and 10,000 tokens, as set in the resource budgets of [FRAMEWORK.md](FRAMEWORK.md). This section cites those numbers and does not change them.

### 2. Character classes outside strings and comments

| Class | Rule |
|---|---|
| Whitespace | space, tab, LF, CRLF. A CR not followed by LF is an error. |
| Identifier | `[A-Za-z_][A-Za-z0-9_]*`, case-sensitive, at most 64 characters. The 64 limit is a v0.1 lexical limit. |
| Punctuation | `{ } [ ] = ; ,` and the `-` sign of a number. |
| Anything else | error (non-ASCII, other punctuation, other control characters) |

### 3. Reserved keywords

`language`, `mirum`, `design`. Keywords are case-sensitive (`Design` is an ordinary identifier). `true`, `false` and `null` are reserved for later versions and are rejected as identifiers in v0.1.

### 4. Numbers

The EBNF `number` rule applies, plus:

- No leading zeros except a single `0` directly before `.` or at the end of the integer part (`0`, `0.5` are valid; `007` is an error).
- No leading `+`. No `.` without digits on both sides (`.5` and `5.` are errors).
- At most 32 characters in total.
- A value that is non-finite after conversion to IEEE-754 binary64 is an error.
- A unit is an identifier following the number on the same line, separated by optional spaces or tabs. Unit lookup is semantic (F05), not lexical.

### 5. Strings

- JSON string syntax (RFC 8259), single line.
- Allowed escapes exactly: `\"` `\\` `\/` `\b` `\f` `\n` `\r` `\t` `\uXXXX` (4 hex digits, either case). Any other escape is an error.
- A `\u` high surrogate must be followed immediately by a `\u` low surrogate. An unpaired surrogate is an error.
- Raw U+0000-U+001F and U+007F inside a string are errors; they must be escaped.
- Maximum decoded length is 4096 code points (a v0.1 lexical limit).

### 6. Comments

`//` to end of line. A comment may contain any valid UTF-8 except control characters U+0000-U+0008, U+000B-U+001F (tab is allowed) and U+007F. There are no block comments in v0.1; `/*` is an error.

### 7. Trojan-source guard

The bidirectional control characters U+202A-U+202E, U+2066-U+2069, U+200E and U+200F are rejected everywhere, including strings and comments. String content may express them only through `\u` escapes, which semantic checks may still reject.

### 8. Lexical error codes

Every code has category `lexical`. Each diagnostic carries a source span (byte offset plus line:column, both 1-based, column counted in Unicode scalar values) and an actionable hint, per [Compiler stages and executable output](#compiler-stages-and-executable-output). Lexing stops at the first error in v0.1: one diagnostic, deterministic.

| Code | Meaning |
|---|---|
| MEL-L001 | Invalid UTF-8 |
| MEL-L002 | Byte-order mark at start of input |
| MEL-L003 | NUL byte |
| MEL-L004 | Bare CR (not followed by LF) |
| MEL-L005 | Disallowed character outside string/comment |
| MEL-L006 | Bidirectional control character |
| MEL-L007 | Identifier longer than 64 characters |
| MEL-L008 | Reserved word used as identifier (including true/false/null) |
| MEL-L009 | Malformed number |
| MEL-L010 | Leading zero in number |
| MEL-L011 | Number longer than 32 characters |
| MEL-L012 | Non-finite number after binary64 conversion |
| MEL-L013 | Unterminated string |
| MEL-L014 | Newline in string |
| MEL-L015 | Raw control character in string |
| MEL-L016 | Invalid escape sequence |
| MEL-L017 | Malformed `\u` escape |
| MEL-L018 | Unpaired surrogate escape |
| MEL-L019 | String longer than 4096 code points |
| MEL-L020 | Block comment not supported |
| MEL-L021 | Control character in comment |
| MEL-L022 | Source larger than 64 KiB |
| MEL-L023 | More than 10,000 tokens |

### 9. Implementation

F04 implements these rules with positive and negative fixtures (P06) and may only tighten them through a versioned change recorded in its handoff.

### 10. Parser, syntax codes and formatter (Frozen in F04)

Frozen in F04 (see [F04 handoff](handoffs/F04.md)). One tightening of section 7 (security review of the F04 PR): U+061C (Arabic letter mark), U+2028 and U+2029 are rejected everywhere as MEL-L006, like the listed bidi controls. No other lexical rule changed.

- Semantic check for text fields of the beam lowering: a decoded string containing a control (Cc), invisible format character (Cf, which includes every bidi control) or line/paragraph separator, even when written as an escape, is rejected (IR010).

- Implementation: a hand-written lexer and recursive-descent parser in backend/engineering/language/ (stdlib only, no parser library, no evaluation of source).
- Positions: byte offset 0-based; line and column 1-based, column in Unicode scalar values; spans end inclusive.
- Whole-input check order: size (L022), BOM (L002), UTF-8 (L001); then the first error by position, where a NUL (L003) or bidi control (L006) wins a tie.
- The token budget counts tokens excluding the end-of-input marker.
- Syntax codes (category syntax): MEL-P001 empty source, P002 missing header, P003 unsupported version (only "0.1"), P004 unexpected token, P005 missing `;`, P006 duplicate assignment, P007 content after the design block, P008 nesting deeper than 16, P009 more than 2,000 syntax nodes, P010 list longer than 256 elements, P011 unexpected end of input. A keyword where a name is expected is MEL-L008.
- Budgets: depth counts program 1, assignment 2, value 3, each list level +1; nodes count the program, each assignment and each value.
- A unit is the identifier directly after a number on the same line; an `e`/`E` not followed by digits (or a sign and digits) ends the number.
- Formatter: canonical layout (two-space indent, one assignment per line, `name = value;`, inline lists `[a, b]`, number text as written plus one space and the unit, strings as ASCII JSON escapes, LF, trailing newline). Comments are dropped; the tree and semantic hash are unchanged, the source hash changes.

## Beam fixture

See [beam.mir](examples/beam.mir). Field names below are the proposed language surface, translated to the registered rectangular_cantilever 1.0.0 capability rather than blindly forwarded to Python.

Required fields: capability, capability_version, backend, numerical_policy, resource_policy, length, width, dimensional_tolerance, tip_force, max_tip_deflection, youngs_modulus, allowable_stress, material_reference, material_source_kind, assumptions, mode, candidate_heights and exports. F01 verifies material_source_kind mapping against actual B03 schema and freezes an explicit translation; no unknown value is silently defaulted (frozen in F01 below).

The backend symbolic selector occt resolves to a pinned installed backend before review. It is not “choose whatever works.” source fields contain explicit policy versions. Exports must be supported by the chosen adapter and capability. Other capabilities use their own registered typed field sets; unsupported fields fail, rather than warn and continue.

## material_source_kind translation (Frozen in F01)

Frozen in F01. Verified against the repository at commit 456e622:

- The B03 contract field is `source_kind: Literal['illustrative', 'user_supplied', 'documented']` on `MaterialReference`, at `backend/engineering/contracts.py:102`; its dotted review path `material.source_kind` is listed in `REVIEW_FIELDS` at `backend/engineering/contracts.py:27`. The B03 task card that owns the contracts is [B03-contracts.md](../tasks/B03-contracts.md) (it does not spell out this field; the code is the authority).
- `backend/engineering/plate_spec.py:80` carries the same three values for rectangular_plate.
- `backend/engineering/proposer.py:42` requires that source_kind retain its illustrative/user_supplied/documented meaning, and `backend/engineering/proposer.py:57` declares the optional proposal field with the same three values.

| MEL `material_source_kind` | Contract field | Contract value |
|---|---|---|
| `"illustrative"` | `material.source_kind` | `'illustrative'` |
| `"user_supplied"` | `material.source_kind` | `'user_supplied'` |
| `"documented"` | `material.source_kind` | `'documented'` |

The mapping is an identity of strings. The MEL value must be a string literal, not an identifier.

Rules:

- A missing `material_source_kind`, any other value, a wrong-case value (`"Illustrative"`) or a non-string value is a compilation error. This is semantic error MEL-S001 (provisional name; F05 assigns the final semantic code table).
- There is no default.
- The value is an assertion to review, not certification of physical stock (see Type and semantic checks).
- If the contract enum changes, MEL needs a new versioned translation. No silent reinterpretation of existing programs.

[beam.mir](examples/beam.mir) uses `material_source_kind = "illustrative";`, which maps to `'illustrative'`.

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

Frozen in F05 (see [F05 handoff](handoffs/F05.md)): stages 1-4 are `engineering.language.semantic.compile_source` (default and maximum deadline 2 s, checked at stage boundaries and per assignment and list element); stages 5-7 are `engineering.program.planning` (plan, v0_2 lock, review through the existing BriefService, run gate). The only compilable capability in v0.1 is rectangular_cantilever 1.0.0 on occt with exports exactly step and glb. Every other capability is MEL-S011, including regen_chamber, because the reviewed bridge carries only the beam. Semantic codes have category `semantic`; MEL-S018 has category `resource`. The diagnostic also carries the matching F02 IR code (`ir_code`). The table below replaces the provisional MEL-S001 name above:

| Code | Meaning | IR code |
|---|---|---|
| MEL-S001 | material_source_kind missing, non-string, wrong case or unknown | IR010 |
| MEL-S002 | Unknown field for capability@version | IR002 |
| MEL-S003 | Missing required field (no defaults) | IR010 |
| MEL-S004 | Unknown unit | IR005 |
| MEL-S005 | Unit of the wrong dimension (incl. mass for force) | IR006 |
| MEL-S006 | Unitless physical quantity | IR006 |
| MEL-S007 | Non-finite value after unit normalization | IR007 |
| MEL-S008 | Wrong value kind (string, quantity, list) | IR010 |
| MEL-S009 | Text with a control, format or separator character | IR010 |
| MEL-S010 | Invalid design name | IR010 |
| MEL-S011 | Unsupported capability or capability version | IR010 |
| MEL-S012 | Backend not supported for the capability | IR010 |
| MEL-S013 | Export not supported by the backend/capability | IR010 |
| MEL-S014 | Candidate policy violation (beam: 1-4 unique values in the envelope) | IR010 |
| MEL-S015 | Value rejected by the reviewed brief validation | IR010 |
| MEL-S016 | Invalid source_id label | none |
| MEL-S017 | Reviewed round trip mismatch (fail closed) | IR010 |
| MEL-S018 | Compile deadline exceeded (resource) | none |
| MEL-S019 | Invalid program record from the bridge | IR010 |

AST-to-IR lowering is deterministic for the same supported versions. An execution lock changes when backend/native/solver/numerical policies change. A source-only formatting edit can retain semantic approval only after recomputing and proving semantic equality; provenance still records the changed source.

## Tests and future syntax

Parser/formatter round trips, whitespace/comment equivalence, lexical failures, duplicate keys, unit confusion, nonfinite overflow, invalid versions, budget exhaustion and hostile Python-like input are mandatory. Equivalent .mir and form briefs must satisfy P03/P07.

Later versions may add reusable components, dimensional expressions, assemblies or graphs only after their semantics, budgets, feature identity and evidence obligations are specified. Version migration returns an explicit semantic diff and requires renewed review where meaning changes. Never reinterpret old programs silently.
