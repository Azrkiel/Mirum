"""Real native reviewed-fixture -> durable complete package, without a provider."""
import asyncio
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

from engineering.briefs import BriefService
from engineering.bundles import BundlePublisher,FORMATS
from engineering.contracts import REVIEW_FIELDS,ReviewAcknowledgement
from engineering.jobs import JobController
from engineering.store import SQLiteStore,SQLiteBriefRepository


async def main(output,sha):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    Path('/tmp/mirum').mkdir(parents=True,exist_ok=True)
    payload=json.loads(Path('backend/tests/fixtures/engineering/brief-draft.json').read_text())['content']
    with tempfile.TemporaryDirectory(prefix='engineering-package-',dir='/tmp/mirum') as temporary:
        store=SQLiteStore(temporary)
        publisher=BundlePublisher(store)
        service=BriefService(SQLiteBriefRepository(store))
        prepared=service.create(payload); draft=prepared.draft
        brief=service.finalize(ReviewAcknowledgement(draft_id=draft.draft_id,draft_revision=draft.revision,
            brief_sha256=draft.content.brief_sha256,acknowledged_fields=REVIEW_FIELDS,
            reviewed_at=datetime.now(timezone.utc),reviewed_by='explicit verification caller seam'))
        jobs=JobController(store,publisher=publisher)
        try:
            job=jobs.submit(brief.brief_id,'package-probe')
            await jobs.wait(job.job_id)
            run=jobs.read(job.job_id)
            if run.outcome!='accepted': raise RuntimeError('Native package probe did not accept')
            manifest=dict(tested_implementation_sha=sha,provider_calls=0,
                scope='Real native and durable publication; fixture/caller review, no HTTP/browser/physical qualification',
                job=run.model_dump(mode='json'),files={})
            for format in FORMATS:
                with publisher.lease(job.job_id,format) as download:
                    value=download.file.read()
                    (output/download.filename).write_bytes(value)
                    manifest['files'][download.filename]=dict(bytes=len(value),sha256=hashlib.sha256(value).hexdigest())
            (output/'probe.json').write_text(json.dumps(manifest,indent=2)+'\n')
            print(json.dumps(dict(source_sha=sha,outcome=run.outcome.value,
                heights=[a.candidate.specification.height_mm for a in run.attempts],
                bundle=run.bundle.model_dump(mode='json'))))
        finally:
            await jobs.close(); store.close()


if __name__=='__main__': asyncio.run(main(*sys.argv[1:]))
