"""Revision-aware client transaction. Drafts and attempts never erase acceptance."""
from dataclasses import dataclass, field
import hashlib
import json
import uuid


@dataclass
class DragsterState:
    attempt: str = ''
    accepted: dict | None = None
    files: dict = field(default_factory=dict)
    error: str | None = None

    def begin(self):
        self.attempt = uuid.uuid4().hex
        self.error = None
        return self.attempt

    def fail(self, attempt, message):
        if attempt == self.attempt:
            self.error = message

    def accept(self, attempt, event, files):
        if attempt != self.attempt:
            return False
        if event.get('status') != 'done' or event.get('revision_id') != event.get('run_id'):
            raise ValueError('Incomplete accepted revision')
        if set(files) != {'spec', 'report', 'step', 'glb'}:
            raise ValueError('All accepted downloads are required')
        if event.get('digests') != {fmt: hashlib.sha256(data).hexdigest() for fmt, data in files.items()}:
            raise ValueError('Downloaded artifact digest mismatch')
        report = json.loads(files['report'])
        if (report != event.get('report') or report.get('passed') is not True
                or report.get('revision_id') != event['run_id']
                or json.loads(files['spec']) != event.get('spec')
                or report.get('spec_sha256') != event['digests']['spec']
                or report.get('step_sha256') != event['digests']['step']
                or report.get('preview', {}).get('glb_sha256') != event['digests']['glb']):
            raise ValueError('Downloaded revision evidence mismatch')
        self.accepted, self.files, self.error = event, files, None
        return True
