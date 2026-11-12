"""One resumable bounded V15 run. Never prints credentials, prompts or replies."""
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys

from engineering.briefs import BriefService
from engineering.contracts import BriefContent
from engineering.proposer import BriefProposer,GeminiProposalProvider
from engineering.store import SQLiteStore,SQLiteBriefRepository,SQLiteProposalLedger

CORPUS_SHA256='7fb09dcc06626b7fbe31c0e9a2237792ded90ad292651b33e5d4db2ff29bcfea'


def load_credential(path):
    """Read only a single literal Gemini key; no dotenv dependency/evaluation."""
    values={}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        name,separator,raw=line.strip().removeprefix('export ').partition('=')
        name=name.strip()
        if not separator or name not in ('GEMINI_API_KEY_HEAVY','GEMINI_API_KEY'): continue
        parts=shlex.split(raw,comments=True,posix=True)
        if len(parts)!=1 or re.fullmatch(r'[A-Za-z0-9_-]+',parts[0]) is None:
            raise ValueError('Credential must be one literal key; use the process environment for other formats')
        values[name]=parts[0]
    for name in ('GEMINI_API_KEY_HEAVY','GEMINI_API_KEY'):
        if values.get(name):
            os.environ['GEMINI_API_KEY_HEAVY']=values[name]
            return name
    return None


class ObservedProvider(GeminiProposalProvider):
    def __init__(self): self.failures=[]

    async def generate(self,*args,**kwargs):
        try: return await super().generate(*args,**kwargs)
        except Exception as error:
            # Only safe classifications. Never serialize exception text/requests.
            code=getattr(error,'code',None)
            try: code=code() if callable(code) else code
            except Exception: code=None
            message=str(error).lower()
            self.failures.append(dict(exception=type(error).__name__,code=str(code) if code is not None else None,
                tls_certificate_failure='certificate verify failed' in message or 'certificate_verify_failed' in message))
            raise


def score(case,result):
    prepared=result.prepared
    content=prepared.draft.content
    expected=BriefContent.model_validate_json(json.dumps(case['expected_content'])) if case.get('expected_content') else None
    accurate=bool(case['kind']=='complete' and result.state=='proposed' and not prepared.issues and content and expected
                  and content.brief_sha256==expected.brief_sha256)
    blocked=bool(prepared.issues or content is None or result.state!='proposed')
    # Wrong requirements silently presented as a complete proposal fail closed.
    silent_drop=not blocked and (case['kind']!='complete' or not accurate)
    return dict(case_id=case['id'],kind=case['kind'],expected_state=case['expected_state'],state=result.state,
        accurate_reviewable_draft=accurate,silent_requirement_drop=silent_drop,review_blocked=blocked,
        expected_brief_sha256=expected.brief_sha256 if expected else None,
        actual_brief_sha256=content.brief_sha256 if content else None)


async def main(output,state_root,sha,credentials=None):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    credential_variable=None
    if credentials:
        # One existing Gemini key only. No pool, provider or model substitution.
        credential_variable=load_credential(credentials)
    path=Path('backend/tests/fixtures/engineering/ai-corpus-v1.json')
    corpus=path.read_bytes(); assert hashlib.sha256(corpus).hexdigest()==CORPUS_SHA256,'Fixed corpus changed'
    cases=[{k:v for k,v in case.items() if k!='fixture_response'} for case in json.loads(corpus)['cases']]
    assert len(cases)==30 and Counter(c['kind'] for c in cases)=={'complete':10,'ambiguous':10,'unsupported':10}
    provider=ObservedProvider()
    evidence=dict(tested_implementation_sha=sha,corpus_sha256=CORPUS_SHA256,provider='gemini',
        maximum_calls=60,maximum_calls_per_case=2,credential_variable=credential_variable,
        cases=[],calls=0,unreviewed_executions=0,fixture_response_used=False,qualification='blocked')
    def save():
        (output/'live.json').write_text(json.dumps(evidence,indent=2)+'\n')
    if not provider.available:
        evidence['blocker']='GEMINI_API_KEY_HEAVY unavailable; no live calls made'; save(); print(json.dumps(evidence)); return 2
    evidence['settings']=provider.settings().model_dump(mode='json')
    store=SQLiteStore(state_root)
    store.recover()
    try:
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS v15_cases(id TEXT PRIMARY KEY,draft TEXT NOT NULL,result TEXT)')
            prior=db.execute("SELECT value FROM metadata WHERE key='v15_corpus_sha256'").fetchone()
            if prior: assert prior[0]==CORPUS_SHA256,'Cannot change a started benchmark'
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('v15_corpus_sha256',?)",(CORPUS_SHA256,))
        service=BriefService(SQLiteBriefRepository(store)); ledger=SQLiteProposalLedger(store)
        proposer=BriefProposer(service,ledger=ledger,provider=provider)
        from process_runner import ProcessRunner
        native_calls=[]
        async def forbidden_native(*args,**kwargs):
            native_calls.append(True)
            raise AssertionError('Live preparation cannot execute native work')
        ProcessRunner.run=forbidden_native
        def spent():
            with store.transaction() as db:
                rows=db.execute('SELECT a.record FROM audits a JOIN v15_cases v ON a.draft=v.draft').fetchall()
            return sum(json.loads(row[0])['calls'] for row in rows)
        for case in cases:
            with store.transaction() as db:
                row=db.execute('SELECT * FROM v15_cases WHERE id=?',(case['id'],)).fetchone()
            if row and row['result']:
                evidence['cases'].append(json.loads(row['result'])); continue
            if row:
                # Never repeat a claimed revision after interruption.
                try: audit=ledger.read(row['draft'],1)
                except KeyError: audit=None
                if audit:
                    evidence['blocker']='Interrupted claimed case '+case['id']+'; automatic repeat prohibited'; break
                draft_id=row['draft']
            else:
                draft_id=service.create({}).draft.draft_id
                with store.transaction() as db:
                    db.execute('INSERT INTO v15_cases VALUES (?,?,NULL)',(case['id'],draft_id))
            result=await proposer.propose(draft_id,expected_revision=1,prompt=case['prompt'])
            audit=ledger.read(draft_id,1)
            assert audit.calls<=2
            assert not service.is_finalized(draft_id,result.prepared.draft.revision)
            measured=dict(score(case,result),audit=audit.model_dump(mode='json'),provider_failures=list(provider.failures))
            evidence['cases'].append(measured)
            with store.transaction() as db:
                db.execute('UPDATE v15_cases SET result=? WHERE id=?',(json.dumps(measured),case['id']))
            evidence['calls']=spent()
            evidence['unreviewed_executions']=len(native_calls)
            assert evidence['calls']<=60
            save()
            print(json.dumps(dict(case_id=case['id'],state=result.state,calls=audit.calls,accurate=measured['accurate_reviewable_draft'])),flush=True)
            if provider.failures and (provider.failures[-1]['exception']!='RuntimeError' or provider.failures[-1]['tls_certificate_failure']):
                evidence['blocker']='Configured provider access/runtime failed: '+json.dumps(provider.failures[-1]); break
        evidence['calls']=spent()
        evidence['complete_accurate']=sum(c['accurate_reviewable_draft'] for c in evidence['cases'])
        evidence['silent_requirement_drops']=sum(c['silent_requirement_drop'] for c in evidence['cases'])
        evidence['qualification']='passed' if (len(evidence['cases'])==30 and evidence['complete_accurate']>=8
            and evidence['silent_requirement_drops']==0 and evidence['unreviewed_executions']==0) else 'blocked'
        if evidence['qualification']=='blocked' and 'blocker' not in evidence:
            evidence['blocker']='Fixed corpus accuracy/silent-drop gate failed; benchmark unchanged'
        save(); print(json.dumps({k:evidence[k] for k in ('qualification','calls','complete_accurate','silent_requirement_drops')}))
        return 0 if evidence['qualification']=='passed' else 2
    finally: store.close()


if __name__=='__main__': sys.exit(asyncio.run(main(*sys.argv[1:])))
