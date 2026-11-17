"""Opt-in 15-call sample. Run only with user approval; importing makes no calls."""
import argparse
import asyncio
from datetime import date
import json
import math
import os
from pathlib import Path

from ..extraction import ExtractionUnavailable, interpret


def mismatches(case, result):
    if result.get('status') != 'resolved':
        return [result.get('status', 'unavailable')]
    spec = result['spec']
    errors = []
    if spec.get('style') != case['expected_style']:
        errors.append('style')
    for path, expected in case['expected_fields'].items():
        value = spec
        for part in path.split('.'):
            value = value.get(part) if isinstance(value, dict) else None
        if type(value) not in (int,float) or not math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-8):
            errors.append(path)
    return errors


async def run(output):
    if not os.environ.get('GEMINI_API_KEY_HEAVY'):
        raise RuntimeError('GEMINI_API_KEY_HEAVY is required for the approved live sample')
    cases = json.loads(Path(__file__).with_name('prompts.json').read_text(encoding='utf-8'))
    rows = []
    counts = dict(resolved=0, needs_clarification=0, unsupported=0, mismatch=0, unavailable=0)
    for case in cases:
        try:
            result = await interpret(case['prompt'], None)
        except ExtractionUnavailable:
            result = {'status':'unavailable'}
        errors = mismatches(case, result)
        status = 'mismatch' if result['status'] == 'resolved' and errors else result['status']
        counts[status] += 1
        rows.append(f"| {case['id']} | {status} | {', '.join(errors) or '—'} |")
    summary = '; '.join(f'{name}={count}' for name, count in counts.items())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('# Dragster text live sample\n\n' + summary + '\n\n'
                      '| ID | Outcome | Mismatch |\n|---|---|---|\n' + '\n'.join(rows) + '\n', encoding='utf-8')
    print(summary)


def main():
    parser = argparse.ArgumentParser(description='User-approved live Gemini sample (15 calls)')
    parser.add_argument('--output', type=Path, default=Path('output/qa-reports') / f'{date.today()}-dragster-text.md')
    args = parser.parse_args()
    asyncio.run(run(args.output))


if __name__ == '__main__':
    main()
