"""One bounded B13 diagnostic: one original corpus case through the ordinary proposer.

At most one initial call plus one schema repair. Never prints credentials, prompts or
replies; raw replies stay in the local ledger. A reserved state root is never reused.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

from engineering.briefs import BriefService
from engineering.contracts import BriefContent
from engineering.proposer import BriefProposer
from engineering.store import SQLiteStore, SQLiteBriefRepository, SQLiteProposalLedger
from scripts.checked_engineering_live_eval import (CORPUS_SHA256, ObservedProvider, load_credential,
                                                   safe_audit)

CORPUS = Path('backend/tests/fixtures/engineering/ai-corpus-v1.json')


async def main(output, state_root, credentials=None, *, case_id, authorization):
    if not (isinstance(authorization, str) and authorization.strip()):
        raise ValueError('An explicit authorization identity is required')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    raw = CORPUS.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CORPUS_SHA256, 'Fixed corpus changed'
    cases = {case['id']: case for case in json.loads(raw)['cases']}
    case = cases[case_id]
    credential_variable = load_credential(credentials) if credentials else None
    provider = ObservedProvider()
    report = dict(authorization=authorization, case_id=case_id, credential_variable=credential_variable)
    if not provider.available:
        report['blocker'] = 'GEMINI_API_KEY_HEAVY unavailable; no live calls made'
        (output/'diagnostic.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
        return 2
    store = SQLiteStore(state_root)
    original_native = None
    native_calls = []
    try:
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS live_diagnostic(id TEXT PRIMARY KEY, draft TEXT NOT NULL)')
            if db.execute('SELECT 1 FROM live_diagnostic').fetchone():
                raise ValueError('Diagnostic already reserved in this state root; never repeated')
        service = BriefService(SQLiteBriefRepository(store))
        ledger = SQLiteProposalLedger(store)
        draft_id = service.create({}).draft.draft_id
        with store.transaction() as db:
            db.execute('INSERT INTO live_diagnostic VALUES (?,?)', (case_id, draft_id))
        from process_runner import ProcessRunner
        original_native = ProcessRunner.run

        async def forbidden_native(*args, **kwargs):
            native_calls.append(True)
            raise AssertionError('Live preparation cannot execute native work')
        ProcessRunner.run = forbidden_native
        started = time.monotonic()
        result = await BriefProposer(service, ledger=ledger, provider=provider).propose(
            draft_id, expected_revision=1, prompt=case['prompt'])
        audit = ledger.read(draft_id, 1)
        assert audit.calls <= 2
        content = result.prepared.draft.content
        expected = case.get('expected_content')
        expected_sha = BriefContent.model_validate_json(json.dumps(expected)).brief_sha256 if expected else None
        report.update(settings=provider.settings().model_dump(mode='json'), state=result.state,
                      issues=len(result.prepared.issues), elapsed_seconds=round(time.monotonic() - started, 3),
                      audit=safe_audit(audit), provider_failures=list(provider.failures),
                      native_execution_attempts=len(native_calls),
                      brief_matches_expected=bool(content and expected_sha and content.brief_sha256 == expected_sha))
        (output/'diagnostic.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        return 0 if report['brief_matches_expected'] and not native_calls else 2
    finally:
        if original_native is not None:
            ProcessRunner.run = original_native
        store.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('state_root')
    parser.add_argument('credentials', nargs='?')
    parser.add_argument('--case-id', default='C01')
    parser.add_argument('--authorization', required=True)
    sys.exit(asyncio.run(main(**vars(parser.parse_args()))))
