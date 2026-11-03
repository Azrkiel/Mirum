"""Single-design revisions and shared controls. All dimensions are SI."""
import hashlib
import json
from typing import Literal

from pydantic import Field, create_model, model_validator
from .spec import StrictModel, GliderRequest
from .model.config import CONFIG

VERSION = 'glider-explorer-2'
FAMILIES = ('wing', 'fuselage', 'tail')

def attribute(key):
    return key.removesuffix('_m')

def controls(request=None):
    ranges = (request or GliderRequest()).configuration()['ranges']
    return {family: {attribute(k): {'range': list(v), 'numeric': isinstance(CONFIG['ranges'][family][k], tuple),
                    'key': k, 'unit': 'deg' if k.endswith('_deg') else 'm' if k.endswith('_m') else ''}
                    for k, v in fields.items()} for family, fields in ranges.items()}

def part_model(family):
    fields = {}
    for key, meta in controls()[family].items():
        if key.startswith('naca_'):
            continue
        if meta['numeric']:
            fields[key] = (float | None, None) if family == 'tail' else (float, ...)
        else:
            fields[key] = (str, ...)
    if family == 'wing':
        fields['naca'] = (str, ...)
    return create_model(family.title() + 'Design', __base__=StrictModel, **fields)

WingDesign, FuselageDesign, TailDesign = [part_model(f) for f in FAMILIES]

class Parameters(StrictModel):
    wing: WingDesign
    fuselage: FuselageDesign
    tail: TailDesign

class Placement(StrictModel):
    wing_x: float | None = None
    payload_x: float | None = None
    ballast_kg: float = Field(0, ge=0, le=0.5)
    tail_incidence_deg: float = Field(0, ge=-6, le=3)

class FlightConditions(StrictModel):
    mode: Literal['automatic_trim', 'specified'] = 'automatic_trim'
    airspeed_m_s: float = Field(5, ge=1, le=15, description='True airspeed relative to air, not ground speed')
    alpha_deg: float = Field(4, ge=-10, le=15, description='Angle of attack relative to the body reference')
    beta_deg: float = Field(0, ge=-10, le=10, description='Sideslip angle')
    altitude_m: float = Field(0, ge=0, le=5000, description='ISA altitude above sea level; separate from launch height')

class DesignSpec(StrictModel):
    parameters: Parameters
    flight_conditions: FlightConditions = Field(default_factory=FlightConditions)
    request: GliderRequest = Field(default_factory=GliderRequest)
    placement: Placement = Field(default_factory=Placement)
    balancing_policy: Literal['rebalance_and_retrim'] = Field('rebalance_and_retrim', description='Automatic-trim policy only; specified conditions preserve placement')

    @model_validator(mode='after')
    def supported(self):
        metadata = controls(self.request)
        for family in FAMILIES:
            part = getattr(self.parameters, family).model_dump()
            for key, meta in metadata[family].items():
                if key.startswith('naca_'):
                    continue
                value = part[key]
                if family == 'tail' and meta['numeric']:
                    relevant = key.startswith('vt_') == (part['kind'] == 'V')
                    if not relevant:
                        if value is not None:
                            raise ValueError(f'Irrelevant tail control: {key}')
                        continue
                allowed = meta['range']
                if value is None or (meta['numeric'] and not allowed[0] <= value <= allowed[1]) or (not meta['numeric'] and value not in allowed):
                    raise ValueError(f'Unsupported {family}.{key}')
        code = self.parameters.wing.naca
        space = self.request.configuration()['ranges']['wing']
        if (len(code) != 4 or not code.isascii() or not code.isdigit()
            or int(code[0]) not in space['naca_camber_pct']
            or (int(code[0]) == 0 and code[1] != '0')
            or (int(code[0]) != 0 and int(code[1]) not in space['naca_camber_pos'])
            or int(code[2:]) not in space['naca_thickness_pct']):
            raise ValueError('Unsupported wing airfoil')
        # Placement is evidence, not a way to escape the permitted mounting interval.
        gl = self.build()
        lo, hi = gl.wing_saddle()
        if self.placement.wing_x is not None and not lo - 1e-9 <= self.placement.wing_x <= hi + 1e-9:
            raise ValueError('Wing position outside allowed saddle')
        if self.placement.payload_x is not None:
            bay = gl.payload_bay()
            if not bay or not bay[0] - 1e-9 <= self.placement.payload_x <= bay[1] + 1e-9:
                raise ValueError('Payload position outside bay')
        return self

    def build(self):
        from .model.components import WingParams, FuselageParams, TailParams
        from .model.assembly import Glider
        parts = [getattr(self.parameters, f).model_dump(exclude_none=True) for f in FAMILIES]
        return Glider(WingParams(id='W01', **parts[0]), FuselageParams(id='F01', **parts[1]),
                      TailParams(id='T01', **parts[2]), self.request.configuration(), **self.placement.model_dump())

    def digest(self):
        physical = self.model_dump()
        # Sampling settings do not affect a single physical design.
        for key in ('evaluations', 'top_k', 'seed'):
            physical['request'].pop(key)
        return digest(physical)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def placement(gl):
    return dict(wing_x=gl.wing_position()[0], payload_x=gl.payload_x,
                ballast_kg=gl.ballast_kg, tail_incidence_deg=gl.tail_incidence_deg)

def from_candidate(candidate):
    parts = json.loads(json.dumps(candidate['parameters']))
    for family, part in parts.items():
        part.pop('id', None)
        if family == 'tail':
            for key in list(part):
                if key not in ('kind', 'airfoil') and key.startswith('vt_') != (part['kind'] == 'V'):
                    del part[key]
    return DesignSpec(parameters=parts, request=candidate['request'], placement=dict(
        wing_x=candidate.get('wing_le_x_m'), payload_x=candidate.get('payload_x_m'),
        ballast_kg=candidate.get('ballast_kg', 0), tail_incidence_deg=candidate.get('tail_incidence_deg', 0)))

def preset(request=None):
    request = request or GliderRequest()
    parts = {}
    for family, fields in controls(request).items():
        part = {k: (sum(m['range']) / 2 if m['numeric'] else m['range'][0]) for k, m in fields.items()
                if not k.startswith('naca_')}
        if family == 'tail':
            part = {k: v for k, v in part.items() if k in ('kind', 'airfoil') or k.startswith('vt_') == (part['kind'] == 'V')}
        parts[family] = part
    defaults = {
        'wing': dict(span=1.1, root_chord=.15, taper=.6, sweep_qc_deg=0, dihedral_deg=7, washout_deg=1.5, incidence_deg=2),
        'fuselage': dict(length=.75, max_diameter=.07, nose_frac=.08, pod_frac=.38, boom_radius=.007, wing_station_in_pod=.5),
        'tail': dict(h_span=.32, h_root_chord=.08, h_taper=1, v_height=.12, v_root_chord=.09, v_taper=.7, v_sweep_le_deg=20)}
    for family, values in defaults.items():
        for key, value in values.items():
            if key in parts[family]:
                lo, hi = controls(request)[family][key]['range']
                parts[family][key] = min(hi, max(lo, value))
    r = request.configuration()['ranges']['wing']
    m = 2 if 2 in r['naca_camber_pct'] else r['naca_camber_pct'][0]
    t = 8 if 8 in r['naca_thickness_pct'] else r['naca_thickness_pct'][0]
    p = (4 if 4 in r['naca_camber_pos'] else r['naca_camber_pos'][0]) if m else 0
    if '0008' in request.configuration()['ranges']['tail']['airfoil']:
        parts['tail']['airfoil'] = '0008'
    parts['wing']['naca'] = f'{m}{p}{t:02d}'
    return DesignSpec(parameters=parts, request=request)
