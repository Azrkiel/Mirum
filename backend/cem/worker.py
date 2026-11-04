"""Private capped worker; JSON stdin, no inherited provider credentials."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(1, str(Path(__file__).resolve().parents[2]))
sys.path.append('/opt')  # trusted analysis package mount in docker-compose.yml
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
    elif payload['kind'] == 'comparison':
        from cem.glider.comparison import compare_shape
        compare_shape(req, payload['calibration'], payload['elite'], directory,
                      independent_recheck=payload.get('independent_recheck', False))
    else:
        raise ValueError('Unknown CEM stage')


if __name__ == '__main__':
    main()
