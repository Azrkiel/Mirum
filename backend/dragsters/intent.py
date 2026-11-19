"""Small, provider-free route detector. Negatives override vehicle intent."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Intent:
    is_dragster: bool
    reason: str


def detect(prompt: str) -> Intent:
    text = prompt.lower().replace('₂', '2')
    text = re.sub(r'carbon\s+dioxide|co\s*2(?:-powered)?', 'co2', text)
    tokens = re.findall(r'\b[a-z0-9]+\b', re.sub(r'\brace\s+car\b', 'racecar', text))
    if (re.search(r'\bpinewood\b|\bfull[ -]size\b|\blife[ -]size\b|\b1\s*:\s*1\b|'
                  r'\breal\s+car\b|\btop\s+fuel\b|\bnhra\b|\bfunny\s+car\b', text)
            or ('derby' in tokens and 'co2' not in tokens)):
        return Intent(False, 'Excluded vehicle category')
    vehicle = {'dragster', 'car', 'racer', 'racecar', 'vehicle'}
    if not vehicle.intersection(tokens):
        return Intent(False, 'No dragster vehicle intent')
    if 'dragster' in tokens:
        return Intent(True, 'Explicit dragster')
    near = any(abs(i - j) <= 4 for i, word in enumerate(tokens) if word == 'co2'
               for j, other in enumerate(tokens) if other in vehicle)
    return Intent(near, 'CO2 near vehicle' if near else 'No CO2 near vehicle')
