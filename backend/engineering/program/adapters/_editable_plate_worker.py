"""Bounded native entry point for building or freshly rereading an editable plate."""

from base64 import b64decode, b64encode
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from vendor.mirum_editable_plate import Plate, PlateError, build_step, inspect_step


def main():
    request = json.load(sys.stdin)
    operation = request['operation']
    try:
        plate = Plate(**request['specification'])
        if operation == 'build':
            step = build_step(plate)
        elif operation == 'inspect':
            step = b64decode(request['step'], validate=True)
        else:
            raise ValueError('unsupported editable plate operation')
        output = {'report': inspect_step(plate, step)}
        if operation == 'build':
            output['step'] = b64encode(step).decode('ascii')
    except PlateError as error:
        output = {'report': {'outcome': 'fail', 'diagnostics': [error.diagnostic]}}
    print(json.dumps(output, allow_nan=False))


if __name__ == '__main__':
    main()
