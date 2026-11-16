"""Fixed native worker. Only strict JSON is read from stdin; no source execution."""
from pathlib import Path
import sys

# Fixed repository import root, independent of the private scratch cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragsters.spec import DragsterSpec
from dragsters.local import generate_result


def main():
    try:
        spec = DragsterSpec.model_validate_json(sys.stdin.buffer.read(16385))
        generate_result(spec, Path(sys.argv[1]))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
