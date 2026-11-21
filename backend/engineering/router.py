"""Provider-free capability router (U7a). Stdlib rules only; negatives override.

No model, network, numpy or CadQuery. The dragster decision is
``dragsters.intent.detect`` unchanged. When more than one capability matches
the router returns ``None`` with a reason: it never guesses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from dragsters.intent import detect

ROUTER_VERSION = 'capability_router_v1'

CHECKED = 'checked_design'
UNVERIFIED = 'unverified_geometry'


@dataclass(frozen=True)
class RouteDecision:
    capability_id: str | None
    reason: str
    assurance: str


def _norm(prompt: str) -> str:
    return re.sub(r'\s+', ' ', prompt.lower().replace('-', ' ')).strip()


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def _regen(text: str) -> tuple[bool, str]:
    if _has(r'\bhose\b|\bgarden\b|3d print(?:er|ing)? nozzle|\bprinter nozzle\b|\bextruder\b|\bhot ?end\b'
            r'|\bpressure washer\b|\bspray\b|\bregenerative brak|\bcpu\b|\bstove\b|\bfirefight', text):
        return False, 'Excluded nozzle or cooling context'
    regen = _has(r'\bregen(?:erative(?:ly)?)?\b', text) and _has(r'\bcool(?:ed|ing)?\b|\bchannels?\b', text)
    channels = _has(r'\b(?:cooling|coolant) channels?\b', text)
    rocket = _has(r'\brocket\b|\bthrust chamber\b|\bcombustion chamber\b|\bengine\b|\bbell nozzle\b', text)
    thrust = _has(r'\bthrust chamber\b|\bnozzle\b|\bchamber\b|\bengine\b|\brocket\b', text)
    if (regen or channels) and rocket and thrust and (regen or _has(r'\bnozzle\b|\bthrust chamber\b|\bchamber\b', text)):
        return True, 'Regeneratively cooled rocket chamber or nozzle with cooling channels'
    return False, 'No regenerative rocket chamber intent'


def _glider(text: str) -> tuple[bool, str]:
    if not _has(r'\bglider\b|\bgliders\b', text):
        return False, 'No glider intent'
    if _has(r'\bhang glider\b|\bsailplane\b|\bpaper (?:air)?plane\b|\bsugar glider\b|\bporch glider\b'
            r'|\bfull size\b|\blife size\b|\bcruise\b|\baircraft\b|\bwing box\b|\bthermals?\b|\bridge\b'
            r'|\bsoaring\b|\bmotor(?:ized)?\b|\bpowered\b|\belectric\b|\bservo\b|\brc\b|\bradio\b'
            r'|\brail\b|\bdrawer\b|\bchair\b|\bcage\b|\b\d+(?:\.\d+)? ?m\b span\b|\bspan \d+(?:\.\d+)? ?m\b', text):
        return False, 'Excluded glider category'
    launch = _has(r'\bhand ?(?:launch(?:ed)?|toss(?:ed)?|thrown)\b|\btoss(?:ed)?\b|\bpassive\b|\bcatapult'
                  r'|\bhi ?start\b|\bdiscus\b|\bdrop(?:ped)? ?(?:launch(?:ed)?)?\b|\blaunched by hand\b'
                  r'|\bhand launch\b', text)
    mission = _has(r'\bdistance\b|\brange\b|\bduration\b|\bflight time\b', text)
    if launch and mission:
        return True, 'Passive or hand-launched glider distance or duration design'
    return False, 'Glider without a supported launch and objective'


def _beam(text: str) -> tuple[bool, str]:
    if _has(r'\blaser\b|\bi beam\b|\bh beam\b|\bt beam\b|\bbridge\b|\btruss\b|\bsnap fit\b|\bsnap\b'
            r'|\bhollow\b|\bcircular\b|\bround\b|\btube\b|\btubular\b|\btapered\b|\bhole\b|\bholes\b'
            r'|\bsimply supported\b|\bcompass\b|\broof\b|\bbalcony\b|\bchannel section\b', text):
        return False, 'Excluded beam category'
    if not (_has(r'\bcantilever\b', text) and _has(r'\bbeam\b', text)):
        return False, 'No cantilever beam intent'
    if _has(r'\brectangular\b|\brectangle\b', text) and _has(
            r'\bsiz(?:e|ing)\b|\bthickness\b|\btip (?:force|load)\b|\bdeflect\w*\b|\bdisplacement\b'
            r'|\ballowable\b|\bstress\b', text):
        return True, 'Rectangular cantilever beam sizing request'
    return False, 'Cantilever beam without rectangular sizing intent'


def _plate(text: str) -> tuple[bool, str]:
    if _has(r'\bholes?\b|\bcut ?outs?\b|\bslots?\b|\bbolt\w*|\bmounting\b|\bbase\b|\badapter\b|\bbrackets?\b'
            r'|\bflanges?\b|\bgussets?\b|\bend plates?\b|\bcover plates?\b|\bbacking plates?\b|\bpressure plates?\b'
            r'|\bstiffen\w*|\bribs?\b|\bribbed\b|\bcircular\b|\bround\b|\bcircle\b|\bannular\b|\btriangular\b'
            r'|\bskew\w*|\belliptic\w*|\bhexagonal\b|\bfree edges?\b|\bone free\b|\bcantilever\w*|\bbeams?\b|\bgirder\b'
            r'|\bpoint (?:load|force|support)\w*|\bconcentrated\b|\bpatch\b|\bbuckl\w*|\bin plane\b|\bcompress\w*'
            r'|\bcomposites?\b|\blaminat\w*|\bsandwich\b|\borthotropic\b|\banisotropic\b|\bthermal\b|\btemperature\b'
            r'|\bdynamic\b|\bimpact\b|\bvibrat\w*|\bfatigue\b|\bcyclic\b|\blarge deflection\b|\bmembrane\b'
            r'|\bheat exchanger\b|\blicen[sc]e\b|\bname ?plate\b|\bnumber plate\b|\barmou?r\b|\bballistic\b'
            r'|\bfood\b|\bdinner\b|\bserving\b|\bdish\b|\bcold plate\b|\bhot plate\b|\bmixed\b|\bpartial\w*'
            r'|\btapered\b|\bvariable thickness\b|\bstepped\b|\bcorrugated\b|\bcurved\b|\bplates\b', text):
        return False, 'Excluded plate category'
    if not (_has(r'\bplate\b', text) and _has(r'\brectangular\b|\brectangle\b|\bflat\b|\bpanel\b', text)):
        return False, 'No rectangular plate intent'
    if not _has(r'\buniform(?:ly)?\b|\bdistributed\b|\bpressure\b', text):
        return False, 'Plate without a uniform pressure load'
    if not _has(r'\bsimply supported\b|\bclamped\b|\bfixed edges?\b|\bfixed on all\b|\bedges? (?:are )?fixed\b', text):
        return False, 'Plate without a supported edge condition'
    if _has(r'\bsiz(?:e|ing)\b|\bthickness\b|\bdeflect\w*\b|\bdisplacement\b|\bstress\b', text):
        return True, 'Rectangular plate bending sizing request under uniform pressure'
    return False, 'Plate without sizing intent'


def route(prompt: str) -> RouteDecision:
    text = _norm(prompt)
    matches: list[tuple[str, str]] = []
    drag = detect(prompt)
    if drag.is_dragster:
        matches.append(('co2_dragster', drag.reason))
    for cap, rule in (('regen_chamber', _regen), ('glider_cem', _glider),
                      ('rectangular_cantilever', _beam), ('rectangular_plate', _plate)):
        ok, why = rule(text)
        if ok:
            matches.append((cap, why))
    if len(matches) == 1:
        return RouteDecision(matches[0][0], matches[0][1], CHECKED)
    if matches:
        ids = ', '.join(sorted(m[0] for m in matches))
        return RouteDecision(None, f'Ambiguous: matches {ids}', UNVERIFIED)
    return RouteDecision(None, 'No registered capability matched', UNVERIFIED)
