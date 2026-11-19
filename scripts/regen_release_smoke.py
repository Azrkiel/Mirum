"""Regen smoke test run INSIDE the release backend container (stdlib only; piped via `docker exec -i`).

The container runs read-only with --network none, so everything goes to the loopback API with the
access token. Env: SMOKE_TOKEN, SMOKE_SPEC (reference spec JSON). Prints one JSON record on stdout
and exits non-zero unless the reference job is ACCEPTED on the browser route (the gate). The checked (v1)
route is run and recorded too; it must match the browser route when it delivers, but a refusal there is
recorded, not fatal (see the budget note below).
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = 'http://127.0.0.1:8000'
TOKEN = os.environ['SMOKE_TOKEN']
SPEC = json.loads(os.environ['SMOKE_SPEC'])
record = {'steps': {}}


def call(method, path, body=None, raw=False):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'X-Mirum-Access-Token': TOKEN, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            content = r.read()
            return r.status, (content if raw else json.loads(content))
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500]
    except (urllib.error.URLError, OSError) as e:  # server not listening yet (startup) -> caller retries
        return 0, repr(e)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def peak_rss_bytes():
    for p in ('/sys/fs/cgroup/memory.peak', '/sys/fs/cgroup/memory/memory.max_usage_in_bytes'):
        try:
            return int(open(p).read())
        except (OSError, ValueError):
            continue
    return None


def fail(msg):
    record['error'] = msg
    print(json.dumps(record, indent=2, sort_keys=True))
    sys.exit(msg)


# 1. the server is up and reports regen as available
for _ in range(90):
    status, caps = call('GET', '/engineering/v1/capabilities')
    if status == 200:
        break
    time.sleep(2)
else:
    fail(f'capabilities never became ready: {status} {caps!r}')
entry = next((c for c in caps['capabilities'] if c['id'] == 'regen_chamber'), None)
if entry is None or entry.get('available') is not True:
    fail(f'regen_chamber is not available in /engineering/v1/capabilities: {entry!r}')
status, cfg = call('GET', '/engineering/regen/config')
if status != 200 or cfg.get('ready') is not True:
    fail(f'/engineering/regen/config says the builder is not ready: {status} {cfg!r}')
record['steps']['capabilities'] = {'regen_chamber_available': True, 'builder_ready': True}

# 2. browser route
t0 = time.monotonic()
status, job = call('POST', '/engineering/regen/jobs', SPEC)
if status != 202:
    fail(f'regen job rejected at submit: {status} {job!r}')
while job['status'] in ('queued', 'running'):
    if time.monotonic() - t0 > 840:
        fail('regen job did not finish in time')
    time.sleep(2)
    status, job = call('GET', '/engineering/regen/jobs/' + job['id'])
wall_old = time.monotonic() - t0
if job['status'] != 'accepted':
    fail(f'browser-route job not accepted: {job.get("status")} {job.get("message")!r}')
files = {}
for name, url in job['artifacts'].items():
    status, content = call('GET', url, raw=True)
    if status != 200 or not content:
        fail(f'artifact {name} not downloadable: {status}')
    files[name] = content
report = json.loads(files['report'])
if (report.get('passed') is not True or report.get('geometry_source') != 'picogk_builder'
        or report.get('engineering_certified') is not False or not files['stl'].startswith(b'solid')
        and len(files['stl']) < 84):
    fail('report fields are not those of an accepted builder run')
record['steps']['browser_route'] = {'status': 'accepted', 'wall_seconds': round(wall_old, 2),
                                    'artifact_sha256': {k: sha(v) for k, v in sorted(files.items())},
                                    'artifact_bytes': {k: len(v) for k, v in sorted(files.items())}}

# 3. checked (v1) route: draft -> reviewed brief -> job -> accepted with a downloadable published bundle
ref = dict(id=entry['id'], version=entry['version'], numerical_policy_version=entry['numerical']['version'],
           resource_policy_version=entry['resources']['version'])
status, made = call('POST', '/engineering/v1/drafts', {'capability': 'regen_chamber',
                                                       'input': {'capability': ref, 'specification': SPEC}})
if status != 201:
    fail(f'draft rejected: {status} {made!r}')
draft = made['draft']
content = draft['content']


def leaves(payload, prefix=''):  # same rule as engineering.capabilities._leaves
    names = []
    for key in sorted(payload):
        value = payload[key]
        names += leaves(value, f'{prefix}{key}.') if isinstance(value, dict) and value else [f'{prefix}{key}']
    return names


review = dict(draft_id=draft['draft_id'], draft_revision=draft['revision'], brief_sha256=made['brief_sha256'],
              acknowledged_fields=['specification.' + n for n in leaves(content['specification'])],
              reviewed_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), reviewed_by='release-smoke')
status, brief = call('POST', '/engineering/v1/briefs', review)
if status != 201:
    fail(f'brief rejected: {status} {brief!r}')
t1 = time.monotonic()
status, sub = call('POST', '/engineering/v1/jobs', {'brief_id': brief['brief_id'], 'idempotency_key': 'release-smoke-1'})
if status != 202:
    fail(f'v1 job rejected at submit: {status} {sub!r}')
job_id = sub['job_id']
while True:
    status, view = call('GET', '/engineering/v1/jobs/' + job_id)
    if view['lifecycle'] in ('completed', 'unavailable', 'cancelled'):
        break
    if time.monotonic() - t1 > 840:
        fail('v1 job did not finish in time')
    time.sleep(2)
wall_v1 = time.monotonic() - t1
if view['lifecycle'] != 'completed' or view['outcome'] != 'accepted' or view.get('delivery') != 'available':
    # Informational, not a gate: the checked route stores every artifact under a 32 MiB run budget
    # (capabilities.py ResourceLimits.run_bytes), and the reference/small cleaned STLs are 108/68 MB, so
    # the checked route cannot deliver them today. The browser route above is the acceptance gate.
    record['steps']['checked_route'] = {'accepted': False, 'lifecycle': view['lifecycle'],
                                        'outcome': view.get('outcome'), 'delivery': view.get('delivery'),
                                        'failures': view.get('failures'), 'wall_seconds': round(wall_v1, 2),
                                        'gate': False}
    record['peak_container_memory_bytes'] = peak_rss_bytes()
    record['accepted'] = True
    print(json.dumps(record, indent=2, sort_keys=True))
    sys.exit(0)
v1 = {}
for fmt in ('generator-cleaned-stl', 'generator-report', 'generator-spec-canonical'):
    status, content = call('GET', f'/engineering/v1/jobs/{job_id}/artifacts/{fmt}', raw=True)
    if status != 200 or not content:
        fail(f'v1 artifact {fmt} not downloadable: {status}')
    v1[fmt] = content
if v1['generator-report'] != files['report'] or v1['generator-spec-canonical'] != files['spec']:
    fail('v1 report/spec bytes differ from the browser route')
record['steps']['checked_route'] = {'lifecycle': view['lifecycle'], 'outcome': view['outcome'],
                                    'delivery': view['delivery'], 'wall_seconds': round(wall_v1, 2),
                                    'artifact_sha256': {k: sha(v) for k, v in sorted(v1.items())},
                                    'identical_report_and_spec_bytes': True}
record['peak_container_memory_bytes'] = peak_rss_bytes()
record['accepted'] = True
print(json.dumps(record, indent=2, sort_keys=True))
