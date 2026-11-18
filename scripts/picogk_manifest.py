"""Write the PicoGK build manifest (JSON): sha256 of every exported file, image ids, pins.

Usage: picogk_manifest.py EXPORT_DIR OUT_JSON REGEN_IMAGE_ID TIMINGS_JSON
Reads base-image digests and PICOGK_RUNTIME_SHA from the Dockerfiles in the repo.
"""
import hashlib
import json
import re
import sys
from pathlib import Path


def main(argv):
    export, out, image_id, timings = Path(argv[1]), Path(argv[2]), argv[3], Path(argv[4])
    root = Path(__file__).resolve().parents[1]
    rt = (root / 'spike-picogk' / 'Dockerfile.runtime').read_text(encoding='utf-8')
    bd = (root / 'picogk-builder' / 'Dockerfile').read_text(encoding='utf-8')
    bases = sorted(set(re.findall(r'^FROM\s+(\S+@sha256:[0-9a-f]{64})', rt + '\n' + bd, re.M)))
    files = {p.relative_to(export).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(export.rglob('*')) if p.is_file() and p != out}
    manifest = {
        'picogk_runtime_sha': re.search(r'PICOGK_RUNTIME_SHA=(\w+)', rt).group(1),
        'base_images': bases,
        'regen_image_id': image_id,
        'files_sha256': files,
        'timings_seconds': json.loads(timings.read_text()) if timings.exists() else {},
        'consumers': {'REGEN_BUILDER': '/opt/regen/app/builder (from builder/app/)',
                      'REGEN_GCC_LIB': '/opt/regen/lib (from builder/gcc-lib/)'},
    }
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'{len(files)} files hashed; {len(bases)} base images')


if __name__ == '__main__':
    main(sys.argv)
