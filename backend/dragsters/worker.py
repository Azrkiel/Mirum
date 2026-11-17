"""Fixed native worker. Only strict JSON is read from stdin; no source execution."""
from pathlib import Path
import sys

# Fixed repository import root, independent of the private scratch cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragsters.spec_v2 import parse_any
from dragsters.local import generate_result, generate_result_v2


def main():
    try:
        payload = sys.stdin.buffer.read(16385)
        if len(payload) > 16384:
            raise ValueError('Dragster spec exceeds 16 KiB')
        spec = parse_any(payload.decode('utf-8'))
        (generate_result if spec.schema_version == 1 else generate_result_v2)(spec, Path(sys.argv[1]))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
