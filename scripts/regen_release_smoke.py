"""Regen smoke test run INSIDE the release backend container (stdlib only; piped via `docker exec -i`).

The container runs read-only with --network none, so everything goes to the loopback API with the
access token. Env: SMOKE_TOKEN, SMOKE_SPEC (reference spec JSON), SMOKE_RUN (run number; repeated runs in one
container use distinct idempotency keys and must each pass, so run 1's leftovers cannot exhaust scratch). Prints one JSON record on stdout
and exits non-zero unless the reference job is ACCEPTED on BOTH routes (both gate): the browser route and
the checked (v1) route. The checked route's artifact hashes must equal the browser route's. The measured
stored bytes of the checked run are recorded (steps.checked_route.stored_bytes_measured).
"""
import hashlib
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

METADATA_RESERVE = 1024 ** 2  # backend/engineering/bundles.py METADATA_RESERVE, counted in stored_bytes

BASE = 'http://127.0.0.1:8000'
TOKEN = os.environ['SMOKE_TOKEN']
SPEC = json.loads(os.environ['SMOKE_SPEC'])
RUN = os.environ.get('SMOKE_RUN', '1')
if not RUN.isdigit() or len(RUN) > 3:
    sys.exit('SMOKE_RUN must be a small run number')
record = {'run': int(RUN), 'steps': {}}


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


def current_memory_bytes():
    for p in ('/sys/fs/cgroup/memory.current', '/sys/fs/cgroup/memory/memory.usage_in_bytes'):
        try:
            return int(open(p).read())
        except (OSError, ValueError):
            continue
    return None


class PeakSampler(threading.Thread):
    """Samples the container cgroup's current memory every 50 ms; ``peak`` is the highest sample.
    memory.peak itself is since container start (it includes the browser route), so the checked
    route is measured by sampling over its own window."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak = None
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            v = current_memory_bytes()
            if v is not None and (self.peak is None or v > self.peak):
                self.peak = v
            self.stop_event.wait(0.05)

    def finish(self):
        self.stop_event.set()
        self.join(2)
        v = current_memory_bytes()
        if v is not None and (self.peak is None or v > self.peak):
            self.peak = v
        return self.peak


# measurements.builder is written by picogk-builder/Program.cs (the one JSON line) and passed through
# unchanged by regen_cli.run_builder -> regen_mesh.verify. These are per-run statistics, not geometry.
BUILDER_STATS_KEYS = frozenset({'volume_mm3', 'tris', 'seconds', 'peak_mb'})


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
        or report.get('engineering_certified') is not False):
    fail('report fields are not those of an accepted builder run')
stl = files['stl']
if not stl.startswith(b'solid'):
    # Binary STL: 80-byte header, uint32 triangle count, 50 bytes per triangle.
    if len(stl) < 84 or len(stl) != 84 + 50 * int.from_bytes(stl[80:84], 'little'):
        fail('cleaned STL is neither ASCII nor a well-formed binary STL')
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
sampler = PeakSampler()
sampler.start()
status, sub = call('POST', '/engineering/v1/jobs', {'brief_id': brief['brief_id'], 'idempotency_key': f'release-smoke-{RUN}'})
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
sampler.finish()
if view['lifecycle'] != 'completed' or view['outcome'] != 'accepted' or view.get('delivery') != 'available':
    # Gate: an accepted reference spec must deliver on the checked route. blocked_by is derived from the
    # actual failure the API reported, never a fixed explanation.
    blocked_by = '; '.join(map(str, view.get('failures') or ())) or (
        f"lifecycle={view['lifecycle']} outcome={view.get('outcome')} delivery={view.get('delivery')}")
    record['steps']['checked_route'] = {'accepted': False, 'lifecycle': view['lifecycle'],
                                        'blocked_by': blocked_by,
                                        'outcome': view.get('outcome'), 'delivery': view.get('delivery'),
                                        'failures': view.get('failures'), 'wall_seconds': round(wall_v1, 2),
                                        'gate': True}
    fail(f'checked route did not deliver the accepted reference run: {blocked_by}')
v1 = {}
for fmt in ('generator-cleaned-stl', 'generator-report', 'generator-spec-canonical', 'zip'):
    status, content = call('GET', f'/engineering/v1/jobs/{job_id}/artifacts/{fmt}', raw=True)
    if status != 200 or not content:
        fail(f'v1 artifact {fmt} not downloadable: {status}')
    v1[fmt] = content
pairs = {'stl': 'generator-cleaned-stl', 'report': 'generator-report', 'spec': 'generator-spec-canonical'}
differs = {k: {'browser_sha256': sha(files[k]), 'v1_sha256': sha(v1[f])} for k, f in pairs.items()
           if v1[f] != files[k]}
if 'report' in differs:
    # Each route runs its own builder, and the report embeds that run's builder statistics
    # (measurements.builder), which are not reproducible. Every other report field, including the
    # STL digests and every check, must still be identical; the STL and spec bytes must be exact.
    ra, rb = json.loads(files['report']), json.loads(v1['generator-report'])
    for r in (ra, rb):
        stats = r.get('measurements', {}).pop('builder', None)
        if stats is not None:
            unknown = sorted(set(stats) - BUILDER_STATS_KEYS) if isinstance(stats, dict) else ['<not an object>']
            if unknown:
                fail(f'measurements.builder has keys outside the per-run statistics allowlist: {unknown}')
    if ra == rb:
        del differs['report']
        record.setdefault('notes', []).append('report differs only in measurements.builder (per-run builder stats)')
    else:
        differs['report']['top_level_keys_differing'] = sorted(k for k in set(ra) | set(rb) if ra.get(k) != rb.get(k))
        differs['report']['measurement_keys_differing'] = sorted(
            k for k in set(ra.get('measurements', {})) | set(rb.get('measurements', {}))
            if ra.get('measurements', {}).get(k) != rb.get('measurements', {}).get(k))
if differs:
    record['steps']['checked_route'] = {'accepted': True, 'gate': True, 'differs_from_browser_route': differs}
    fail('v1 artifact bytes differ from the browser route: ' + ', '.join(sorted(differs)))
with zipfile.ZipFile(io.BytesIO(v1['zip'])) as z:
    uncompressed = sum(i.file_size for i in z.infolist())
# publisher stored_bytes = every stored payload + manifest + package.zip + METADATA_RESERVE
stored_bytes = uncompressed + len(v1['zip']) + METADATA_RESERVE
record['steps']['checked_route'] = {'accepted': True, 'gate': True, 'lifecycle': view['lifecycle'],
                                    'outcome': view['outcome'], 'delivery': view['delivery'],
                                    'wall_seconds': round(wall_v1, 2),
                                    'artifact_sha256': {k: sha(v) for k, v in sorted(v1.items())},
                                    'artifact_bytes': {k: len(v) for k, v in sorted(v1.items())},
                                    'identical_to_browser_route': 'stl,spec bytes; report except measurements.builder',
                                    'stored_bytes_measured': stored_bytes,
                                    'peak_memory_bytes': sampler.peak}
record['peak_container_memory_bytes'] = peak_rss_bytes()
record['gate'] = 'browser_route+checked_route'
record['accepted'] = True
print(json.dumps(record, indent=2, sort_keys=True))
