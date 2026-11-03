"""Private bounded worker entrypoint; no provider credentials are inherited."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gliders.spec import GliderRequest
from gliders.search import search


def main():
    output = pathlib.Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else 'search'
    payload = sys.stdin.read()
    if mode == 'evaluation':
        from gliders.design_spec import DesignSpec
        from gliders.evaluation import run_evaluation
        run_evaluation(DesignSpec.model_validate_json(payload), output)
        return
    spec = GliderRequest.model_validate_json(payload)

    def progress(value):
        temporary = output / 'progress.tmp'
        temporary.write_text(json.dumps(value))
        temporary.replace(output / 'progress.json')

    search(spec, output, progress)


if __name__ == '__main__':
    main()
