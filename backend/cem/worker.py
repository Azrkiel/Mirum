"""Private capped worker; JSON stdin, no inherited provider credentials."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cem.glider.requirements import GliderMissionRequirements
from cem.glider.service import run_discovery, build_design


def main():
    directory = Path(sys.argv[1])
    payload = json.loads(sys.stdin.buffer.read(262145))
    req = GliderMissionRequirements.model_validate(payload['requirements'])

    def progress(value):
        temporary = directory / 'progress.tmp'
        temporary.write_text(json.dumps(value, allow_nan=False))
        temporary.replace(directory / 'progress.json')

    if payload['kind'] == 'discovery':
        run_discovery(req, payload['calibration'], directory, progress)
    elif payload['kind'] == 'design':
        build_design(req, payload['calibration'], payload['elite'], directory)
    else:
        raise ValueError('Unknown CEM stage')


if __name__ == '__main__':
    main()
