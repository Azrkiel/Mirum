"""Local JSON-to-verified-STEP command with private staging per attempt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from pydantic import ValidationError

from .spec import DragsterSpec, canonical_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Generate and verify one deterministic wedge body')
    parser.add_argument('--spec', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        spec = DragsterSpec.model_validate_json(args.spec.read_text(encoding='utf-8'))
    except (ValidationError, UnicodeError) as exc:
        print(f'Invalid dragster JSON/spec: {exc}', file=sys.stderr)
        return 2
    except OSError as exc:
        print(f'Cannot read spec: {exc}', file=sys.stderr)
        return 1

    stage = None
    try:
        # Invalid JSON is rejected before loading any native geometry code.
        import cadquery as cq
        from .geometry import build_body
        from .validation import verify_step

        root = args.output_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.dragster-stage-', dir=root))
        step = stage / 'body.step'
        cq.exporters.export(build_body(spec), str(step), exportType='STEP')
        report = verify_step(step, spec)
        if report.get('passed') is not True:
            raise ValueError('STEP verification did not pass')
        (stage / 'spec.json').write_text(canonical_spec(spec), encoding='utf-8')
        (stage / 'report.json').write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        if not all((stage / name).is_file() and (stage / name).stat().st_size > 0
                   for name in ('body.step', 'spec.json', 'report.json')):
            raise ValueError('Incomplete result files')
        destination = root / f'dragster-{uuid.uuid4().hex}'
        if destination.exists():
            raise FileExistsError('Fresh result name is already occupied')
        stage.rename(destination)
        stage = None
        print(destination)
        return 0
    except Exception as exc:
        print(f'Dragster generation failed: {exc}', file=sys.stderr)
        return 1
    finally:
        if stage is not None:
            try:
                shutil.rmtree(stage)
            except OSError as exc:
                print(f'Cannot clean this attempt staging directory: {exc}', file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
