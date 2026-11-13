"""Editor metadata shared across styles; no CAD imports."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldInfo:
    label: str
    unit: str
    help: str
    group: str


GROUPS = ('Body & shape', 'Axles & wheels', 'Cartridge pocket', 'Stock block', 'Material')


def field_info(path):
    if path.startswith('wheels.') or 'axle' in path:
        group = 'Axles & wheels'
    elif path.startswith('pocket_'):
        group = 'Cartridge pocket'
    elif path.startswith('stock.'):
        group = 'Stock block'
    elif path.startswith('material.'):
        group = 'Material'
    else:
        group = 'Body & shape'
    unit = 'mm' if path.endswith('_mm') else 'g/cm³' if path.endswith('density_g_cm3') else 'ratio'
    help_text = path.replace('_mm', '').replace('_', ' ').replace('.', ': ').capitalize()
    if 'height' in path:
        help_text += '; height above body bottom'
    if path.endswith('_x_mm'):
        help_text += '; X measured from the nose'
    if path.startswith('wheels.') and 'diameter' in path:
        help_text += '; axle Z is half this diameter; wheel contact is ground Z=0'
    return FieldInfo(path, unit, f'{help_text} ({unit})', group)
