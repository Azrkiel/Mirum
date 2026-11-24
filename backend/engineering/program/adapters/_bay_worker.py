"""Bounded native subprocess for the pinned equipment-bay kernel."""

from base64 import b64decode, b64encode
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

# The backend directory is fixed by the installed script, never by a caller.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from engineering.equipment_bay import kernel_inputs, selected_rotation


def main():
    from vendor.mirum_kernel import build_delivery, inspect_delivery, inspect_step, parse_design, search_layout
    from vendor.mirum_kernel.exchange import BOM, STEP, bom_record

    request = json.load(sys.stdin)
    design_json, supplier = kernel_inputs(request['specification'])
    search = search_layout(design_json, substitution=supplier)
    angle = selected_rotation(search)
    design = parse_design(design_json, substitution=supplier, rotation_deg=angle)
    if request['operation'] == 'inspect':
        step = b64decode(request['step'], validate=True)
        output = {'report': inspect_step(design, step), 'bom': bom_record(design), 'search': search}
    elif request['operation'] == 'build':
        with TemporaryDirectory() as parent:
            delivery = Path(parent) / 'delivery'
            build_delivery(design, delivery)
            output = {'report': inspect_delivery(design, delivery),
                      'search': search,
                      'step': b64encode((delivery / STEP).read_bytes()).decode('ascii'),
                      'bom': json.loads((delivery / BOM).read_bytes())}
    else:
        raise ValueError('unsupported worker operation')
    print(json.dumps(output, allow_nan=False))


if __name__ == '__main__':
    main()
