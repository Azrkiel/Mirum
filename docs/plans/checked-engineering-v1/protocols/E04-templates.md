# E04 templates (blank, no data)

All fields are blank placeholders. Nothing in this file is a measurement.

## Sheet A: specimen record

| Field | Value |
|---|---|
| Specimen ID | |
| Family / capability version | |
| CAD revision and STEP identity (hash) | |
| Material, supplier, grade, lot, certificate | |
| Process, orientation, finish | |
| Date made, maker | |
| Nominal L, b, h (mm) | |
| Measured L (mm), instrument ID, readings | |
| Measured b at 3 stations (mm) | |
| Measured h at 3 stations (mm) | |
| Mass (g) and scale ID | |
| Visual defects | |

## Sheet B: equipment and calibration

| Instrument | ID | Class / resolution | Certificate no. | Calibration date | Due date | Range |
|---|---|---|---|---|---|---|
| | | | | | | |

## Sheet C: test session

| Field | Value |
|---|---|
| Session date, operator, witness | |
| Specimen ID, support condition, clamp torque | |
| Load point, load method | |
| Temperature, humidity | |
| Gauge zero before / after | |

| Run | Load level | Up / down | Reading 1 | Reading 2 | Reading 3 | Mean deflection (mm) | Notes |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Sheet D: uncertainty budget

| Term | Source | Distribution | Standard uncertainty | Sensitivity coeff. | Contribution |
|---|---|---|---|---|---|
| | | | | | |

Combined standard uncertainty, coverage factor k, expanded uncertainty: blank.

## Sheet E: comparison

| Specimen | Load | Predicted P | U_P | Measured M | U_M | M - P | Limit | Pass / fail / not evaluated |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

Pre-set limits and their signature date must be filled before Sheet C is used.

## Sheet F: report header and claims

Specimen IDs, material, load, geometry revision, test conditions, deviations from protocol, outliers and reasons,
claims supported (per the protocol's evidence table), claims not supported, operator signature and date,
reviewer signature and date. Raw data files attached by hash.
