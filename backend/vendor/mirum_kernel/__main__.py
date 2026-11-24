"""Run the equipment-bay design, delivery and inspection operations."""

from argparse import ArgumentParser
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from OCP.Standard import Standard_Failure

from . import KernelError, build_delivery, inspect_delivery, load_design, search_layout
from .design import ORIENTATIONS_DEG
from .exchange import json_text


def _write_report(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json_text(result))
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest="operation", required=True)
    validate = operations.add_parser("validate", help="load and validate an editable design")
    build = operations.add_parser("build", help="export a STEP delivery and BOM")
    inspect = operations.add_parser("inspect", help="measure delivered STEP against the design")
    search = operations.add_parser("search", help="evaluate both permitted bay orientations")
    for command in (validate, build, inspect, search):
        command.add_argument("design", type=Path)
        command.add_argument("--substitution", type=Path)
    for command in (validate, build, inspect):
        command.add_argument("--rotation", type=int, choices=ORIENTATIONS_DEG, help="override the authored angle")
    build.add_argument("--output", type=Path, required=True)
    inspect.add_argument("--delivery", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.operation == "search":
            substitution = args.substitution.read_bytes() if args.substitution is not None else None
            result = search_layout(args.design.read_bytes(), substitution=substitution)
            print(json_text(result), end="")
            return 0 if result["outcome"] == "feasible" else 1
        design = load_design(args.design, substitution=args.substitution, rotation_deg=args.rotation)
        if args.operation == "validate":
            print(f"{design.id}: valid design with {len(design.modules)} modules")
            return 0
        if args.operation == "build":
            print(build_delivery(design, args.output))
            return 0
        result = inspect_delivery(design, args.delivery)
        report = args.delivery / "inspection.json"
        _write_report(report, result)
        print(f"{report} ({result['outcome']})")
        return 0 if result["outcome"] == "pass" else 1
    except (KernelError, OSError, Standard_Failure) as error:
        print(f"mirum_kernel: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
