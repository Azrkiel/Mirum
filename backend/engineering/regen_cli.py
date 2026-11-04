"""regen_cli <spec.json> <outdir>: validate, build with the fixed PicoGK builder, verify, emit.

Exit codes: 0 accepted, 2 rejected (spec or mesh checks), 1 unavailable (builder crash, timeout,
malformed output, missing runtime), 3 prebuilt STL passed the checks but is not accepted because
the builder did not make it. On non-zero exit no accepted artifact (cleaned.stl, report.json) is
written. No LLM, no network. The builder process is disposable; its failure is an ordinary result.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

from .regen_spec import RegenChamberSpec, canonical_spec

BUILDER_TIMEOUT_S = 600
MAX_SPEC_BYTES = 65536
DEFAULT_BUILDER = '/app/builder'
DEFAULT_GCC_LIB = '/usr/local/lib/picogk-gcc'
RUNTIME_SO = 'picogk.26.2.so'
SAFETY_NOTICE = ('Geometry checks only. No gas dynamics, thermal, structural, or manufacturing analysis was done. '
                 'Do not manufacture or fire hardware based on this output.')

EXIT_ACCEPTED, EXIT_UNAVAILABLE, EXIT_REJECTED, EXIT_PREBUILT_PASSED = 0, 1, 2, 3


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_spec(path: Path) -> RegenChamberSpec:
    raw = path.read_bytes()
    if len(raw) > MAX_SPEC_BYTES:
        raise ValueError('spec file too large')
    return RegenChamberSpec.model_validate(json.loads(raw))


def run_builder(builder: Path, spec_path: Path, stl_path: Path):
    """Returns (stats dict, None) or (None, error string)."""
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = f'{os.environ.get("REGEN_GCC_LIB", DEFAULT_GCC_LIB)}:{builder.parent}'  # builder only
    try:
        p = subprocess.run([str(builder), str(spec_path), str(stl_path)], env=env, capture_output=True,
                           timeout=BUILDER_TIMEOUT_S, cwd=str(stl_path.parent))
    except subprocess.TimeoutExpired:
        return None, f'builder timed out after {BUILDER_TIMEOUT_S} s'
    except OSError as e:
        return None, f'builder could not start: {e}'
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or b'')[-400:].decode('utf-8', 'replace')
        return None, f'builder exited {p.returncode}: {tail.strip()}'
    lines = [ln for ln in p.stdout.decode('utf-8', 'replace').splitlines() if ln.startswith('{')]  # PicoGK also logs to stdout
    try:
        stats = json.loads(lines[-1])
        if not isinstance(stats, dict):
            raise ValueError
    except (IndexError, ValueError):
        return None, 'builder output was not one JSON line'
    if not stl_path.is_file() or stl_path.stat().st_size < 84:
        return None, 'builder produced no STL'
    return stats, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='regen_cli')
    ap.add_argument('spec')
    ap.add_argument('outdir')
    ap.add_argument('--prebuilt-stl', help='verify an existing STL instead of running the builder (test hook)')
    args = ap.parse_args(argv)

    try:
        spec = load_spec(Path(args.spec))
    except (ValidationError, ValueError, OSError) as e:
        print(f'REJECTED spec: {e}', file=sys.stderr)
        return EXIT_REJECTED

    from . import regen_mesh  # numpy/scipy only needed past this point

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.work-', dir=outdir))
    try:
        spec_path = work / 'spec.canonical.json'
        spec_path.write_text(canonical_spec(spec))
        builder = Path(os.environ.get('REGEN_BUILDER', DEFAULT_BUILDER))
        stats, so_hash = None, ''
        if args.prebuilt_stl:
            raw = Path(args.prebuilt_stl)
        else:
            raw = work / 'raw.stl'
            stats, err = run_builder(builder, spec_path, raw)
            if err:
                print(f'UNAVAILABLE: {err}', file=sys.stderr)
                return EXIT_UNAVAILABLE
        so = builder.parent / RUNTIME_SO
        if so.is_file():
            so_hash = regen_mesh.sha256_file(so)
        elif not args.prebuilt_stl:
            print(f'UNAVAILABLE: runtime {RUNTIME_SO} not found beside the builder', file=sys.stderr)
            return EXIT_UNAVAILABLE
        try:
            raw_hash = regen_mesh.sha256_file(raw)
            tris = regen_mesh.read_stl(raw)
        except (ValueError, OSError) as e:
            print(f'UNAVAILABLE: malformed builder output: {e}', file=sys.stderr)
            return EXIT_UNAVAILABLE
        report, cleaned = regen_mesh.verify(spec, tris, raw_stl_sha256=raw_hash, runtime_so_sha256=so_hash,
                                            builder_stats=stats)
        report['geometry_source'] = 'prebuilt_test_hook' if args.prebuilt_stl else 'picogk_builder'
        report['safety_notice'] = SAFETY_NOTICE
        print(f'NOTICE: {SAFETY_NOTICE}', file=sys.stderr)
        body = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
        if not report['passed']:
            _atomic_write(outdir / 'rejected_report.json', body)
            print('REJECTED checks: ' + '; '.join(report['failures'][:10]), file=sys.stderr)
            return EXIT_REJECTED
        if args.prebuilt_stl:
            _atomic_write(outdir / 'prebuilt_report.json', body)
            print('PREBUILT passed checks; not accepted because the builder did not make it', file=sys.stderr)
            return EXIT_PREBUILT_PASSED
        _atomic_write(outdir / 'spec.canonical.json', spec_path.read_bytes())
        _atomic_write(outdir / 'cleaned.stl', regen_mesh.stl_bytes(cleaned))
        _atomic_write(outdir / 'report.json', body)  # last: its presence marks acceptance
        print(json.dumps({'accepted': True, 'stl_sha256': report['stl_sha256'],
                          'raw_stl_sha256': report['raw_stl_sha256']}))
        return EXIT_ACCEPTED
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
