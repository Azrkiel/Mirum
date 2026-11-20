# E04 physical validation protocol (draft, not executed)

State: PROTOCOL ONLY. The owner has no specimens, fixture, load cell or dial gauge. No measurement exists, and
none may be invented. Limits are fixed before any test; values marked TBD are set by the responsible engineer at
sign-off. Templates: [E04-templates.md](E04-templates.md). Blocked handoff: [../handoffs/E04.md](../handoffs/E04.md).

## Scope

Beam: rectangular cantilever per [CAPABILITY.md](../CAPABILITY.md) (clamp at X=0, transverse end force at X=L,
both section dimensions at most L/10, small deflection). Plate: only after the E03 contract is approved
([proposal](../proposals/E03-plate-bending.md)); its fixture follows the approved edge condition.

## Specimens

- Material: record supplier, grade, lot, certificate if any. Properties come from the certificate or a tensile
  coupon test, never assumed. Same lot for all specimens.
- Process: record machining or printing method, orientation, finishing.
- Geometry: reviewed thickness set (several values within the supported range), at least 3 specimens per
  thickness (count TBD). Each specimen has an ID bound to the CAD revision and STEP identity it was made from.
- Measure each specimen before testing: length, width, thickness at several stations, with the instruments below.

## Fixture and loading

- Beam: rigid clamp, clamped length TBD multiples of thickness, flat clamp faces, recorded clamp torque. Load at a
  recorded point by calibrated mass or load cell, transverse, no twist. Specimen self-weight accounted for.
- Plate: fixture matches the approved edge condition; pressure by an approved method (bladder, vacuum or
  distributed mass), recorded.
- Load levels: several levels within the predicted allowable (range TBD), zero reading before and after each,
  loading and unloading, repeated runs per specimen.
- Stay inside the model's small-deflection limit; stop at the declared deflection bound or any sign of yield.

## Equipment classes and calibration

| Quantity | Class | Calibration requirement |
|---|---|---|
| Length, width, thickness | caliper or micrometer; resolution well below the tolerance being claimed | certificate or gauge-block check, dated |
| Force | load cell or calibrated masses | traceable certificate in date; range covers the loads |
| Deflection | dial gauge or displacement transducer | checked against gauge blocks; resolution recorded |
| Temperature | thermometer | one reading per session |

Instrument IDs, certificate numbers and dates go on every sheet. Out-of-date calibration invalidates the session.

## Uncertainty budget method (GUM style)

Per measured result: list each term, convert to a standard uncertainty (from certificates and repeat data, never
assumed), apply sensitivity coefficients, combine in quadrature, expand with a stated coverage factor. Terms:
- instrument resolution and calibration (each instrument);
- repeatability (repeat loadings and readings);
- specimen geometry (thickness variation matters most: stiffness scales with `h^3`);
- material modulus (certificate or coupon scatter);
- clamp compliance and slip (estimated by a reference or stiffer-clamp test);
- load point, alignment and gravity effects;
- temperature, creep and time at load;
- gauge zero drift.
Budget and acceptance limits are written and signed before measurement.

## Comparison and acceptance rules

Prediction `P` comes from the verified run bound to the revision; measurement `M` has expanded uncertainty `U_M`;
`U_P` is input uncertainty propagated through the model.
- Agreement if `|M - P| <= k * sqrt(U_M^2 + U_P^2)` and the relative difference is under a fixed limit; `k` and the
  limit are set in advance (TBD).
- Disagreement is a finding: investigate clamp, material, geometry, process, then record it.
- Never tune the model to match, never drop outliers without a recorded reason, never use a simulation as data.
- Observed data, simulations and copied fixtures are labelled separately.

## Evidence required before any claim

| Claim | Needed evidence |
|---|---|
| tested | signed sheets for named specimens, calibrated equipment, uncertainty budget, comparison result |
| manufacturable | process-specific tolerances, inspection instructions, a built and inspected specimen |
| validated for the model range | agreement across several thicknesses and load levels within pre-set limits |
| certified | outside this project; needs a standards body or responsible engineer release |

No claim beyond the evidence. Without specimens and equipment, no physical or manufacturing claim is made.

## Responsible engineer

A named operator signs the protocol before testing and the report after. Not yet assigned.
