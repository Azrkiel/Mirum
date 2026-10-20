"""Trusted native/FE worker. Generated code runs only through the existing compiler."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from engineering.spec import CantileverSpec
    from engineering.verify import verify_step
    step_path, spec_path, report_path = map(Path, sys.argv[1:])
    spec = CantileverSpec.model_validate_json(spec_path.read_text(encoding='utf-8'))
    report = verify_step(str(step_path), spec)
    report_path.write_text(json.dumps(report, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()

