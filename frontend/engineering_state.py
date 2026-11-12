"""Small revision-bound state shared by the checked UI and its regressions."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib


def identity(prepared):
    if prepared is None: return None
    draft=prepared['draft']
    return draft['draft_id'],draft['revision'],prepared.get('brief_sha256')


@dataclass
class CheckedState:
    prepared: dict | None=None
    brief: dict | None=None
    job_id: str | None=None
    stale: bool=False

    def adopt_draft(self,prepared):
        if identity(prepared)!=identity(self.prepared) or self.stale: self.brief=None
        self.prepared=deepcopy(prepared)
        self.stale=False

    def observe(self,prepared):
        if self.prepared is None:
            self.adopt_draft(prepared)
        elif identity(prepared)!=identity(self.prepared):
            self.stale=True
            self.brief=None
        return not self.stale

    def reviewed(self,brief):
        expected=identity(self.prepared)
        actual=(brief['draft_id'],brief['draft_revision'],brief['brief_sha256'])
        if self.stale or actual!=expected: raise ValueError('Review belongs to another displayed revision')
        self.brief=deepcopy(brief)

    @property
    def can_start(self):
        return bool(self.brief and not self.stale and self.prepared and not self.prepared.get('issues')
            and (self.brief['draft_id'],self.brief['draft_revision'],self.brief['brief_sha256'])==identity(self.prepared))


def merge_form(original,edited):
    result=deepcopy(original)
    for key,value in edited.items():
        result[key]=merge_form(result.get(key,{}) if isinstance(result.get(key),dict) else {},value) if isinstance(value,dict) else deepcopy(value)
    return result


def checked_bytes(job,path,value):
    if job.get('delivery')!='available' or job.get('outcome')!='accepted':
        raise ValueError('Accepted delivery is unavailable')
    bundle=job['bundle']
    if path=='package.zip': expected=bundle['zip_sha256']
    elif path=='manifest.json': expected=bundle['manifest_sha256']
    else: expected=next((p['sha256'] for p in bundle['payloads'] if p['path']==path),None)
    if expected is None or hashlib.sha256(value).hexdigest()!=expected:
        raise ValueError('Downloaded bytes belong to another accepted revision')
    return value
