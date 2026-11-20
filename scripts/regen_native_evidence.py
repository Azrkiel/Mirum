"""Run one regen spec through regen_cli and write a JSON evidence record (stdlib only).

Usage (inside the picogk-regen image): regen_native_evidence.py SPEC OUTDIR EVIDENCE_JSON
Records exit code, wall-clock seconds, peak RSS of the process tree (ru_maxrss of waited
children, KiB on Linux) and sha256 of every output file.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def child_env() -> dict[str, str]:
    allowed = ('PATH', 'PYTHONPATH', 'REGEN_BUILDER', 'REGEN_GCC_LIB', 'HOME', 'TMPDIR')
    secret_markers = ('KEY', 'TOKEN', 'SECRET', 'PASS', 'AUTH', 'CREDENTIAL')
    env = {name: value for name, value in os.environ.items()
           if (name in allowed or name.startswith('DOTNET_'))
           and not any(marker in name.upper() for marker in secret_markers)}
    env.update(OPENBLAS_NUM_THREADS='2', OMP_NUM_THREADS='2')
    return env


def main(argv):
    import resource

    spec, outdir, evidence = (Path(a) for a in argv[1:4])
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, '-m', 'engineering.regen_cli', str(spec), str(outdir)],
                          env=child_env(), capture_output=True, text=True)
    wall = time.monotonic() - t0
    peak_kib = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    files = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
             for p in sorted(outdir.iterdir()) if p.is_file()}
    rec = {'spec': spec.name, 'spec_sha256': hashlib.sha256(spec.read_bytes()).hexdigest(),
           'exit_code': proc.returncode, 'wall_seconds': round(wall, 3), 'peak_rss_kib': peak_kib,
           'outputs': files, 'stdout_tail': proc.stdout[-2000:], 'stderr_tail': proc.stderr[-2000:]}
    evidence.write_text(json.dumps(rec, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({k: rec[k] for k in ('spec', 'exit_code', 'wall_seconds', 'peak_rss_kib')}))
    return proc.returncode


if __name__ == '__main__':
    sys.exit(main(sys.argv))
