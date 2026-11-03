"""Private bounded worker entrypoint; no provider credentials are inherited."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gliders.spec import GliderRequest
from gliders.search import search


def main():
    output = pathlib.Path(sys.argv[1])
    spec = GliderRequest.model_validate_json(sys.stdin.read())

    def progress(value):
        temporary = output / 'progress.tmp'
        temporary.write_text(json.dumps(value))
        temporary.replace(output / 'progress.json')

    search(spec, output, progress)


if __name__ == '__main__':
    main()
