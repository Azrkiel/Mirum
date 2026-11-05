"""Section-method validity rule (docs/implementation/cem-low-re-method-decision.md).

Kept dependency-free so the parent evidence validators can apply the same rule
as the worker-side evaluator.
"""
import math

CONFIDENCE_FLOOR = .8
# P1: below this, integral boundary-layer polars (XFOIL, and NeuralFoil which is
# trained on XFOIL) are outside method validity. Rejected, never penalized.
REYNOLDS_FLOOR = 50000.
# P1 degraded band [REYNOLDS_FLOOR, REYNOLDS_DEGRADED_BELOW): admitted with a
# widened profile-drag prior. 70,000 is Lissaman's (1983) critical Re.
REYNOLDS_DEGRADED_BELOW = 70000.
# Engineering placeholder, not a published value: the degraded-band profile-drag
# factor reaches +30% at its 90th percentile (so P10 performance carries it).
# Replace with measured UIUC error (decision P4), then G7 flight calibration.
DEGRADED_CD_FACTOR_P90 = 1.3
DEGRADED_CD_LOG_SIGMA = math.log(DEGRADED_CD_FACTOR_P90) / 1.2815515655446004
DECISION = 'cem-low-re-method-decision-2026-11-05'


def station_validity(confidence, reynolds):
    """Return the validity envelope and a fail reason (None when admitted)."""
    confidence, reynolds = list(confidence), list(reynolds)
    if not confidence or not reynolds:
        return dict(decision=DECISION), 'missing station polar evidence'
    finite = all(math.isfinite(c) for c in confidence) and all(math.isfinite(r) for r in reynolds)
    min_conf = min(confidence) if finite else float('nan')
    min_re = min(reynolds) if finite else float('nan')
    envelope = dict(min_confidence=min_conf, min_station_reynolds=min_re,
                    confidence_floor=CONFIDENCE_FLOOR, reynolds_floor=REYNOLDS_FLOOR,
                    reynolds_degraded_below=REYNOLDS_DEGRADED_BELOW,
                    low_re_stations=sum(r < REYNOLDS_DEGRADED_BELOW for r in reynolds) if finite else None,
                    low_re_degraded=bool(finite and min_re < REYNOLDS_DEGRADED_BELOW),
                    decision=DECISION)
    reasons = []
    if not finite:
        reasons.append('nonfinite station polar evidence')
    else:
        if min_re < REYNOLDS_FLOOR:
            reasons.append(f'section Re below method validity floor ({min_re:.0f} < {REYNOLDS_FLOOR:.0f})')
        if min_conf < CONFIDENCE_FLOOR:
            reasons.append('station polar outside NeuralFoil confidence envelope')
    return envelope, '; '.join(reasons) or None


def check_envelope(envelope, number):
    """Parent-side check of a reported envelope; `number` is the evidence validator."""
    number(envelope['min_confidence'], CONFIDENCE_FLOOR, 1.)
    min_re = envelope['min_station_reynolds']
    number(min_re, REYNOLDS_FLOOR)
    if envelope.get('low_re_degraded') is not (min_re < REYNOLDS_DEGRADED_BELOW):
        raise ValueError('Low-Re degraded-band flag disagrees with station Reynolds evidence')
