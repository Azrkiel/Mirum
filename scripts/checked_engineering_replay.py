"""Replay a saved complete ZIP without AI; compare digital measurements, not bytes."""
import asyncio
import hashlib
import json
import math
from pathlib import Path
import sys
import zipfile

from engineering.bundles import BundleManifest
from engineering.capabilities import CAPABILITY
from engineering.contracts import EngineeringBrief
from engineering.search import search


async def main(archive,output,sha):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    assert Path(archive).stat().st_size<=CAPABILITY.resources.run_bytes
    with zipfile.ZipFile(archive) as z:
        assert sum(p.file_size for p in z.infolist())<=CAPABILITY.resources.run_bytes
        manifest=BundleManifest.model_validate_json(z.read('manifest.json'))
        assert len(z.namelist())==9 and set(z.namelist())=={p.path for p in manifest.payloads}|{'manifest.json'}
        for p in manifest.payloads:
            data=z.read(p.path)
            assert len(data)==p.bytes and hashlib.sha256(data).hexdigest()==p.sha256
        brief=EngineeringBrief.model_validate_json(z.read('brief.json'))
        original=json.loads(z.read('report.json'))
        previous=json.loads(z.read('attempts.json'))
    assert brief.brief_sha256==manifest.brief_sha256
    result=await search(brief)
    assert result.state=='selected'
    assert [a.outcome.value for a in result.attempts]==[a['outcome'] for a in previous]
    assert [a.candidate.candidate_sha256 for a in result.attempts]==[a['candidate']['candidate_sha256'] for a in previous]
    comparisons=[]
    for old,new in zip(original['evaluations'],result.evaluations):
        before,after=old['native_report'],new['report']
        assert before['implementation_sha256']==after['implementation_sha256']
        assert before['versions']==after['versions']
        for group in ('measurements','physics'):
            a=before[group] if group=='measurements' else before[group].get('solution')
            b=after[group] if group=='measurements' else after[group].get('solution')
            assert bool(a)==bool(b)
            for name,value in (a or {}).items():
                assert math.isclose(value,b[name],rel_tol=CAPABILITY.numerical.metric_relative_error,
                                    abs_tol=CAPABILITY.numerical.metric_absolute_error),(group,name)
                comparisons.append(dict(group=group,name=name,before=value,after=b[name],absolute_difference=abs(value-b[name])))
    evidence=dict(tested_implementation_sha=sha,provider_calls=0,brief_sha256=brief.brief_sha256,
        selected_candidate_sha256=result.selected['candidate'].candidate_sha256,
        verdicts=[a.outcome.value for a in result.attempts],comparisons=comparisons,
        relative_tolerance=CAPABILITY.numerical.metric_relative_error,absolute_tolerance=CAPABILITY.numerical.metric_absolute_error,
        delivered_archive_sha256=hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
        replay_artifacts={name:dict(bytes=len(value),sha256=hashlib.sha256(value).hexdigest()) for name,value in result.selected['artifacts'].items()},
        raw_byte_equality_required=False)
    (output/'replay.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(dict(sha=sha,verdicts=evidence['verdicts'],compared_quantities=len(comparisons),provider_calls=0)))


if __name__=='__main__': asyncio.run(main(*sys.argv[1:]))
