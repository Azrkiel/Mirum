#!/usr/bin/env python3
"""B13 step 12: ONE live proposal through the real review UI of the release stack.

Run by .github/workflows/b13-step12.yml only. The pure parts (case selection, displayed-vs-expected
comparison, call-budget checks, redaction) are unit-tested in backend/tests/test_b13_step12_driver.py
and need only the standard library. Playwright is imported lazily, inside run_browser().

Modes: live (default), --preflight-only (selectors, no spend), --rehearsal (AI off, typed draft).

Budget (owner, 2026-11-19: "Yes, max 3 calls"): at most ONE proposal request, which is the initial
provider call plus the proposer's single schema repair (<= 2 calls), never retried, and no provider call
afterwards. The count is read from the backend's own proposal ledger (the `audits` table, whose `calls`
field the proposer writes before each provider call) through `docker compose exec`, before the
proposal, after it, and after run/poll/download. The backend additionally enforces a hard
MIRUM_ENGINEERING_AI_DAILY_CALLS=3 (e2e/compose/b13-step12.override.yml).

Never prints or stores the access token, the provider key, prompts' raw replies or the ledger. Only
counts, statuses and hashes leave this process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CORPUS_SHA256 = '7fb09dcc06626b7fbe31c0e9a2237792ded90ad292651b33e5d4db2ff29bcfea'
CASE_RE = re.compile(r'C(0[1-9]|10)')   # the ten complete cases only
MAX_TOTAL_CALLS = 3
MAX_PROPOSAL_CALLS = 2
EXIT_PASS, EXIT_FAIL, EXIT_UNAVAILABLE = 0, 1, 3

# The checked UI's reference example (frontend/engineering_ui.py REFERENCE), shown for a typed draft.
# Used only to prove the scrapers work on this exact stack BEFORE any provider call.
REFERENCE_CONTENT = dict(
    schema_version='engineering_brief_v1',
    capability=dict(id='rectangular_cantilever', version='1.0.0', numerical_policy_version='beam_numerical_v1',
                    resource_policy_version='engineering_resources_v1'),
    requirements=dict(units='mm_N_MPa', length_mm=200.0, width_mm=10.0, dimensional_tolerance_mm=0.01,
                      tip_force_n=50.0, max_tip_deflection_mm=1.0,
                      material=dict(youngs_modulus_mpa=70000.0, allowable_stress_mpa=120.0,
                                    source_kind='illustrative',
                                    reference='Illustrative material inputs; not certified stock'),
                      assumptions='ideal_clamp_end_force_linear_elastic'),
    mode='bounded_thickness', candidate_heights_mm=[5.0, 10.0, 15.0])

# Download file name -> API artifact format (frontend/engineering_ui.py FORMATS).
FORMAT_FILES = {'zip': 'package.zip', 'brief': 'brief.json', 'spec': 'selected_spec.json', 'source': 'model.py',
                'step': 'model.step', 'glb': 'preview.glb', 'attempts': 'attempts.json', 'report': 'report.json',
                'markdown': 'report.md', 'manifest': 'manifest.json'}


# ------------------------------------------------------------------ case selection

def load_case(corpus_path, case_id):
    if not isinstance(case_id, str) or CASE_RE.fullmatch(case_id) is None:
        raise ValueError('case_id must be a complete corpus case, C01-C10')
    raw = Path(corpus_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CORPUS_SHA256:
        raise ValueError('Fixed corpus changed')
    for item in json.loads(raw)['cases']:
        if item['id'] == case_id:
            if item['kind'] != 'complete' or not item.get('expected_content'):
                raise ValueError('case is not a complete case')
            return {k: v for k, v in item.items() if k != 'fixture_response'}
    raise ValueError('unknown case')


# ------------------------------------------------------------------ displayed vs expected

_SCALAR = r'"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null'
_KEYED = re.compile(r'(?<![\w.])"?(?P<k>[A-Za-z_]\w*)"?\s*:\s*(?:(?P<open>\[)|(?P<v>' + _SCALAR + '))')


def _scalar(token):
    try:
        return json.loads(token)
    except ValueError:
        return token.strip('"')


def parse_json_view_text(text):
    """Leaf key -> value (lists as lists) from the text of the UI's JSON tree.

    Tolerates quoted or bare keys, `N items` counters, `0:` index prefixes and one-line arrays. Leaf names
    are unique in a brief, so nesting is not needed. Later duplicates overwrite earlier ones.
    """
    found, pos = {}, 0
    while True:
        match = _KEYED.search(text, pos)
        if match is None:
            return found
        if match.group('open'):
            close = text.find(']', match.end())
            close = len(text) if close < 0 else close
            body = re.sub(r'\b\d+\s+items?\b', ' ', text[match.end():close])
            body = re.sub(r'(?<![\w.])\d+\s*:', ' ', body)
            found[match.group('k')] = [_scalar(t) for t in re.findall(_SCALAR, body)]
            pos = close + 1
        else:
            found[match.group('k')] = _scalar(match.group('v'))
            pos = match.end()


def flatten_expected(node, out=None):
    out = {} if out is None else out
    for key, value in node.items():
        if isinstance(value, dict):
            flatten_expected(value, out)
        else:
            if key in out:
                raise ValueError('expected content has a repeated leaf name: ' + key)
            out[key] = value
    return out


def _same(expected, shown):
    if isinstance(expected, bool) or isinstance(shown, bool):
        return expected is shown
    if isinstance(expected, (int, float)) and isinstance(shown, (int, float)):
        return abs(expected - shown) <= 1e-9 * max(1.0, abs(expected))
    if isinstance(expected, list):
        return (isinstance(shown, list) and len(expected) == len(shown)
                and all(_same(a, b) for a, b in zip(expected, shown)))
    return expected == shown


def compare_displayed(expected_content, shown):
    """(ok, rows). A missing displayed value is a mismatch, never a pass."""
    rows = []
    for field, expected in flatten_expected(expected_content).items():
        value = shown.get(field)
        rows.append(dict(field=field, expected=expected, shown=value, match=field in shown and _same(expected, value)))
    return bool(rows) and all(r['match'] for r in rows), rows


_CAPTION = re.compile(r'Draft (\w+) \| revision (\d+) \| brief SHA-256 ([0-9a-f]{64})')


def parse_draft_caption(text):
    match = _CAPTION.search(text or '')
    return dict(draft_id=match.group(1), revision=int(match.group(2)), brief_sha256=match.group(3)) if match else None


# ------------------------------------------------------------------ provider-call accounting

_COUNTER_DEF = '''\
import json, os, sqlite3
from pathlib import Path

def read_counter_from(path):
    out = {"initialized": False, "calls": 0, "rows": 0, "statuses": []}
    if not os.path.exists(path):
        return out
    db = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        try:
            rows = db.execute("SELECT record FROM audits").fetchall()
        except sqlite3.OperationalError:
            return out
        for (record,) in rows:
            data = json.loads(record)
            out["calls"] += int(data.get("calls", 0))
            out["statuses"].append(str(data.get("status")))
        out["rows"] = len(rows)
        out["initialized"] = True
        return out
    finally:
        db.close()
'''
# Runs inside the backend container; prints only {initialized, calls, rows, statuses}.
COUNTER_SCRIPT = _COUNTER_DEF + 'print(json.dumps(read_counter_from("/tmp/mirum/engineering/engineering.sqlite3")))\n'
_namespace: dict = {}
exec(compile(_COUNTER_DEF, '<counter>', 'exec'), _namespace)   # the same source the container runs
read_counter_from = _namespace['read_counter_from']


def classify_proposal(statuses):
    statuses = list(statuses)
    if not statuses or 'running' in statuses:
        return 'running'
    if 'proposed' in statuses:
        return 'proposed'
    if any(s in ('unavailable', 'cancelled', 'conflict') for s in statuses):
        return 'unavailable'
    return 'incomplete'   # clarification_required / unsupported: not a complete reviewable draft


def check_budget(samples):
    """Violations of the owner's budget; empty means it held. Fails closed on anything missing."""
    names = ('before', 'after_proposal', 'after_run', 'final')
    problems = [f'missing checkpoint {n}' for n in names if n not in samples]
    if problems:
        return problems
    for n in names:
        if not samples[n].get('initialized'):
            problems.append(f'ledger not readable at checkpoint {n}')
    before, proposal, run, final = (samples[n] for n in names)
    if before['calls'] != 0 or before['rows'] != 0:
        problems.append(f"before the proposal the ledger already showed {before['calls']} calls in {before['rows']} rows")
    for earlier, later in zip(names, names[1:]):
        if samples[later]['calls'] < samples[earlier]['calls']:
            problems.append(f'ledger calls decreased between {earlier} and {later}')
    if proposal['calls'] - before['calls'] > MAX_PROPOSAL_CALLS:
        problems.append(f"the proposal made {proposal['calls'] - before['calls']} provider calls (limit {MAX_PROPOSAL_CALLS})")
    if proposal['rows'] > 1:
        problems.append('more than one proposal request is recorded; exactly one is allowed')
    if run['calls'] > proposal['calls'] or final['calls'] > proposal['calls']:
        problems.append('provider call recorded after the proposal (run, poll or download must make none)')
    if run['rows'] > proposal['rows'] or final['rows'] > proposal['rows']:
        problems.append('a new proposal ledger row appeared after the proposal')
    if max(s['calls'] for s in (before, proposal, run, final)) > MAX_TOTAL_CALLS:
        problems.append(f'total provider calls exceed {MAX_TOTAL_CALLS}')
    return problems


def check_at_exit(samples, at_exit):
    """Budget check on the last ledger reading, valid on every path including early failures (L1).

    Before the proposal has finished the limit is the one proposal's two calls; afterwards nothing may exceed
    what the proposal itself recorded. Fails closed when the ledger could not be read.
    """
    if not at_exit or not at_exit.get('initialized'):
        return ['ledger not readable at exit']
    problems = []
    done = samples.get('after_proposal')
    limit = done['calls'] if done else MAX_PROPOSAL_CALLS
    if at_exit['calls'] > limit:
        problems.append(f"at exit the ledger shows {at_exit['calls']} calls (limit {limit} at this stage)")
    if at_exit['calls'] > MAX_TOTAL_CALLS:
        problems.append(f'total provider calls exceed {MAX_TOTAL_CALLS}')
    if at_exit['rows'] > 1:
        problems.append('more than one proposal request is recorded; exactly one is allowed')
    if all(n in samples for n in ('before', 'after_proposal', 'after_run', 'final')):
        problems.extend(check_budget(samples))
    return problems


# ------------------------------------------------------------------ access log (M1: un-ledgered provider routes)

# Routes that call a model but are not in the proposal ledger. With the key in the backend they must stay unused.
PROVIDER_ROUTES = ('/generate', '/dragsters/interpret', '/clarify', '/requirements', '/plan', '/refine',
                   '/pmi/extract', '/pmi/annotate')
_ACCESS = re.compile(r'"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (\S+) HTTP/[0-9.]+"')
_ID_SEGMENT = re.compile(r'^(?:[0-9A-Za-z_*-]{16,}|\*+)$')


def normalize_path(raw):
    path = raw.split('?', 1)[0]
    return '/'.join('{id}' if _ID_SEGMENT.match(seg) else seg for seg in path.split('/'))


def count_paths(log_text):
    counts = {}
    for match in _ACCESS.finditer(log_text or ''):
        path = normalize_path(match.group(1))
        counts[path] = counts.get(path, 0) + 1
    return counts


def check_access_log(counts):
    """Violations: any provider route hit, or no engineering request seen (the log would then prove nothing)."""
    problems = [f'provider route requested: {path} x{n}' for path, n in sorted(counts.items())
                if any(path == route or path.startswith(route + '/') for route in PROVIDER_ROUTES)]
    if not any(path.startswith('/engineering/') for path in counts):
        problems.append('access log shows no engineering request; the log check proves nothing')
    return problems


# ------------------------------------------------------------------ redaction

_KEY_SHAPE = re.compile(r'AIza[0-9A-Za-z_\-]{20,}')
_HEADER = re.compile(r'(?i)(x-mirum-access-token\s*[:=]\s*)\S+')


def redact(text, secrets):
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, '[redacted]')
    return _HEADER.sub(r'\1[redacted]', _KEY_SHAPE.sub('[redacted-key]', text))


def scrub(node, secrets):
    if isinstance(node, str):
        return redact(node, secrets)
    if isinstance(node, dict):
        return {k: scrub(v, secrets) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [scrub(v, secrets) for v in node]
    return node


def assert_clean(text, secrets):
    if any(s and len(s) >= 8 and s in text for s in secrets) or _KEY_SHAPE.search(text):
        raise ValueError('a secret survived redaction; nothing is written')


def assert_no_raw_reply(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if 'raw_reply' in key and not key.endswith('_characters'):
                raise ValueError('evidence must not carry raw replies')
            assert_no_raw_reply(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            assert_no_raw_reply(value)


# ------------------------------------------------------------------ backend access (read-only except typed comparison)

class Api:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip('/') + '/engineering/v1', token

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            'X-Mirum-Access-Token': self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=100) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def json(self, method, path, body=None):
        status, _, raw = self.request(method, path, body)
        if status >= 300:
            raise RuntimeError(f'{method} {path} -> HTTP {status}')
        return json.loads(raw)


class Counter:
    def __init__(self, compose):
        self.argv = shlex.split(compose) + ['exec', '-T', 'backend', 'python', '-c', COUNTER_SCRIPT]

    def read(self):
        done = subprocess.run(self.argv, capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise RuntimeError('ledger counter failed (exit %d)' % done.returncode)
        value = json.loads(done.stdout.strip().splitlines()[-1])
        return dict(initialized=bool(value['initialized']), calls=int(value['calls']), rows=int(value['rows']),
                    statuses=[str(s) for s in value['statuses']])


class AccessLog:
    def __init__(self, compose):
        self.argv = shlex.split(compose) + ['logs', '--no-color', '--no-log-prefix', 'backend']

    def paths(self):
        done = subprocess.run(self.argv, capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise RuntimeError('backend log read failed (exit %d)' % done.returncode)
        return count_paths(done.stdout)


# ------------------------------------------------------------------ the UI (every selector lives here)

class UI:
    """Role/label first, Streamlit data-testid second. No emotion classes, no nth-child."""
    EXPANDER = 'Complete normalized input and required checks'
    ACK = re.compile(r'^I reviewed every value')
    # Free text the model controls (questions, notes, unsupported-feature text, the source line, the JSON tree
    # that holds them) is masked in every screenshot. The review table and the numeric fields stay visible.
    MASKED = ('[data-testid="stAlert"]', '[data-testid="stText"]', '[data-testid="stTextInput"]', 'details')
    PROPOSE_ENABLED_JS = ("() => { const b = [...document.querySelectorAll('button')]"
                          ".find(x => x.innerText.trim() === 'Propose draft'); return !!b && !b.disabled; }")
    FORM_NUMBERS = (('length_mm', 'Length (mm)'), ('width_mm', 'Width (mm)'), ('tip_force_n', 'Tip force (+Z, N)'),
                    ('max_tip_deflection_mm', 'Maximum tip displacement (mm)'),
                    ('dimensional_tolerance_mm', 'Digital dimensional tolerance (mm)'))

    def __init__(self, page, base):
        self.page, self.base = page, base

    def open_and_sign_in(self, token):
        p = self.page
        p.goto(self.base + '/?checked_workflow=1')
        gate = p.get_by_label('Access token')
        gate.wait_for(state='visible', timeout=90000)
        gate.fill(token)
        gate.press('Enter')
        self.save_button().wait_for(state='visible', timeout=120000)

    def save_button(self):
        return self.page.get_by_role('button', name='Save draft', exact=True)

    def entry_radio(self):
        return self.page.locator('[data-testid="stRadio"]').filter(has_text='Entry method')

    def entry_radio_option(self, label):
        return self.entry_radio().get_by_text(label, exact=True)

    def prompt_box(self):
        return self.page.get_by_label('Engineering request')

    def propose_button(self):
        return self.page.get_by_role('button', name='Propose draft', exact=True)

    def wait_propose_enabled(self):
        self.page.wait_for_function(self.PROPOSE_ENABLED_JS, timeout=30000)

    def caption_text(self):
        return self.page.locator('[data-testid="stCaptionContainer"]').all_inner_texts()

    def draft_caption(self):
        return parse_draft_caption('\n'.join(self.caption_text()))

    def wait_for_draft(self, changed_from=None, timeout=60000):
        """The caption's draft, once its (draft id, revision) differs from `changed_from`."""
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            shown = self.draft_caption()
            if shown and (shown['draft_id'], shown['revision']) != changed_from:
                return shown
            self.page.wait_for_timeout(500)
        return None

    def review_details(self):
        return self.page.locator('details').filter(has_text=self.EXPANDER).last

    def open_expander(self):
        details = self.review_details()
        details.wait_for(state='attached', timeout=30000)
        if details.get_attribute('open') is None:
            details.locator('summary').click()
        details.locator('[data-testid="stJson"]').first.wait_for(state='visible', timeout=30000)

    def scrape_json_view(self):
        self.open_expander()
        view = self.review_details().locator('[data-testid="stJson"]').first
        for read in (view.inner_text, view.text_content):
            parsed = parse_json_view_text(read() or '')
            if parsed.get('length_mm') is not None:
                return parsed
        return {}

    def material_source_line(self):
        lines = self.page.locator('[data-testid="stText"]').filter(has_text='Material / allowable source:').all_inner_texts()
        return lines[-1].strip() if lines else None

    def start_new_typed_request(self):
        self.page.get_by_role('button', name='Start a new typed request', exact=True).click()
        self.page.get_by_text(re.compile(r'^Reference example with illustrative material values')).wait_for(
            state='visible', timeout=60000)

    def _type_number(self, label, value):
        field = self.page.get_by_label(label, exact=True)
        field.fill(repr(float(value)))
        field.press('Tab')

    def fill_form(self, content):
        """Type the case's values into the checked form (rehearsal only; replaces the AI proposal)."""
        req, material = content['requirements'], content['requirements']['material']
        if material['source_kind'] != 'illustrative' or content['mode'] != 'bounded_thickness':
            raise ValueError('rehearsal form fill supports illustrative bounded_thickness cases only')
        for key, label in self.FORM_NUMBERS:
            self._type_number(label, req[key])
        self._type_number('Elastic modulus (MPa)', material['youngs_modulus_mpa'])
        self._type_number('Allowable stress (MPa)', material['allowable_stress_mpa'])
        reference = self.page.get_by_label('Material / allowable source reference', exact=True)
        reference.fill(material['reference'])
        reference.press('Tab')
        choices = self.page.get_by_label('Thickness choices to review (mm, comma-separated; 1 to 4)', exact=True)
        choices.fill(', '.join(str(float(h)) for h in content['candidate_heights_mm']))
        choices.press('Tab')
        self.save_button().click()

    def ack_checkbox(self):
        return self.page.get_by_text(self.ACK)

    def finalize_button(self):
        return self.page.get_by_role('button', name='Finalize reviewed brief', exact=True)

    def run_button(self):
        return self.page.get_by_role('button', name='Run reviewed beam checks', exact=True)

    def finalized_caption(self):
        return self.page.get_by_text(re.compile(r'^Finalized brief \S+ \| [0-9a-f]{64}'))

    def job_caption_text(self):
        for text in self.caption_text():
            match = re.search(r'Job (\S+) \| brief SHA-256 ([0-9a-f]{64})', text)
            if match:
                return match.group(1), match.group(2)
        return None

    def terminal_state(self):
        return self.page.get_by_text(re.compile(r'^State: (completed|unavailable|cancelled)'))

    def download_buttons(self):
        return self.page.get_by_role('button', name=re.compile(r'^Download '))

    def screenshot(self, directory, name):
        masks = [self.page.locator(selector) for selector in self.MASKED]
        self.page.screenshot(path=str(Path(directory) / name), full_page=True, mask=masks)


# ------------------------------------------------------------------ the run

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def preflight(ui, evidence, shots, token, ai_expected):
    """Provider-free proof of every selector up to the proposal button. Returns the typed draft's identity or None."""
    evidence['stage'] = 'sign-in'
    ui.open_and_sign_in(token)
    ui.screenshot(shots, '01-checked-design.png')
    evidence['stage'] = 'preflight typed draft'
    ui.save_button().click()
    typed_shown = ui.wait_for_draft()
    if typed_shown is None:
        evidence['verdict'], evidence['reason'] = 'fail', 'preflight typed draft never displayed'
        return None
    scraped = ui.scrape_json_view()
    ok, rows = compare_displayed(REFERENCE_CONTENT, scraped)
    evidence['preflight'] = dict(scraper_matches_reference=ok, fields=len(rows), draft_id=typed_shown['draft_id'])
    ui.screenshot(shots, '02-preflight-typed-draft.png')
    if not ok:
        evidence['verdict'], evidence['reason'] = 'fail', 'preflight: displayed-value scraper did not reproduce the reference draft (no provider call was made)'
        evidence['preflight']['mismatches'] = [r['field'] for r in rows if not r['match']]
        return None
    evidence['stage'] = 'preflight entry controls'
    if ai_expected:
        ui.entry_radio_option('AI-assisted draft').click()
        ui.prompt_box().wait_for(state='visible', timeout=30000)
        ui.propose_button().wait_for(state='visible', timeout=30000)
        ui.prompt_box().fill('selector check, never submitted')
        ui.prompt_box().press('Control+Enter')
        ui.wait_propose_enabled()
        evidence['preflight']['ai_controls_found'] = True
    else:
        absent = ui.entry_radio().count() == 0 and ui.propose_button().count() == 0
        evidence['preflight']['ai_controls_absent'] = absent
        if not absent:
            evidence['verdict'], evidence['reason'] = 'fail', 'rehearsal stack shows AI controls; the AI flag must be off'
            return None
    return (typed_shown['draft_id'], typed_shown['revision'])


def run_browser(args, case, token, evidence, counter, api):
    from playwright.sync_api import sync_playwright   # lazy: the unit tests never need it

    shots = Path(args.evidence_dir)
    downloads = Path(args.download_dir)
    samples = evidence['calls']
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chrome, headless=True,
                                     args=['--no-sandbox', '--enable-unsafe-swiftshader'])
        try:
            page = browser.new_page(viewport={'width': 1450, 'height': 1300})
            page.set_default_timeout(60000)
            ui = UI(page, args.ui_url)
            typed_id = preflight(ui, evidence, shots, token, ai_expected=not args.rehearsal)
            if typed_id is None:
                return EXIT_FAIL
            if args.preflight_only:
                evidence['verdict'] = 'preflight-pass'
                return EXIT_PASS

            # The same typed request through the API, kept for the later identity comparison (no provider).
            evidence['stage'] = 'typed comparison draft'
            typed = api.json('POST', '/drafts', dict(mode='form', input=case['expected_content']))
            if typed.get('issues') or not typed.get('brief_sha256'):
                evidence['verdict'], evidence['reason'] = 'fail', 'typed request for the corpus case has issues'
                return EXIT_FAIL
            evidence['typed'] = dict(draft_id=typed['draft']['draft_id'], revision=typed['draft']['revision'],
                                     brief_sha256=typed['brief_sha256'],
                                     fixed_requirements_sha256=typed.get('fixed_requirements_sha256'))

            samples['before'] = counter.read()
            if samples['before']['calls'] != 0 or samples['before']['rows'] != 0 or not samples['before']['initialized']:
                evidence['verdict'], evidence['reason'] = 'fail', 'ledger not clean before the proposal'
                return EXIT_FAIL

            if args.rehearsal:
                # ---- the typed reference draft of the same case, through the same review UI (no provider)
                evidence['stage'] = 'rehearsal typed entry'
                ui.start_new_typed_request()
                ui.fill_form(case['expected_content'])
                evidence['proposal_status'] = 'typed (rehearsal)'
                shown = ui.wait_for_draft(changed_from=typed_id, timeout=60000)
                samples['after_proposal'] = counter.read()
                ui.screenshot(shots, '03-after-typed-entry.png')
                if shown is None:
                    evidence['verdict'], evidence['reason'] = 'fail', 'UI never displayed the typed case draft'
                    return EXIT_FAIL
            else:
                # ---- the one proposal (never retried)
                evidence['stage'] = 'proposal'
                ui.entry_radio_option('AI-assisted draft').click()
                prompt = ui.prompt_box()
                prompt.fill(case['prompt'])
                prompt.press('Control+Enter')
                ui.propose_button().wait_for(state='visible')
                ui.wait_propose_enabled()
                ui.propose_button().click()          # exactly one click, ever
                evidence['proposal_requests'] = 1
                status, deadline = 'running', time.monotonic() + 150
                while time.monotonic() < deadline:
                    page.wait_for_timeout(3000)
                    snapshot = counter.read()
                    status = classify_proposal(snapshot['statuses'])
                    if status != 'running':
                        break
                samples['after_proposal'] = counter.read()
                evidence['proposal_status'] = status
                ui.screenshot(shots, '03-after-proposal.png')
                over = check_budget(dict(before=samples['before'], after_proposal=samples['after_proposal'],
                                         after_run=samples['after_proposal'], final=samples['after_proposal']))
                if over:
                    evidence['verdict'], evidence['reason'], evidence['budget_problems'] = 'fail', 'budget violated at the proposal', over
                    return EXIT_FAIL
                if status == 'unavailable':
                    evidence['verdict'], evidence['reason'] = 'unavailable', 'provider or proposal unavailable; no retry (policy)'
                    return EXIT_UNAVAILABLE
                if status != 'proposed':
                    evidence['verdict'], evidence['reason'] = 'fail', f'proposal ended as {status}; not a complete reviewable draft, nothing acknowledged'
                    return EXIT_FAIL
                shown = ui.wait_for_draft(changed_from=typed_id, timeout=60000)
                if shown is None:
                    evidence['verdict'], evidence['reason'] = 'fail', 'UI never displayed the proposed draft'
                    return EXIT_FAIL

            # ---- check the displayed values BEFORE the acknowledgement
            evidence['stage'] = 'compare displayed values'
            scraped = ui.scrape_json_view()
            ok, rows = compare_displayed(case['expected_content'], scraped)
            source_line = ui.material_source_line()
            expected_line = 'Material / allowable source: ' + case['expected_content']['requirements']['material']['reference']
            evidence['displayed_vs_expected'] = dict(
                fields=rows, all_match=ok, material_source_line_match=source_line == expected_line,
                displayed_brief_sha256=shown['brief_sha256'], typed_brief_sha256=evidence['typed']['brief_sha256'],
                brief_sha256_match=shown['brief_sha256'] == evidence['typed']['brief_sha256'])
            ui.screenshot(shots, '04-draft-before-acknowledgement.png')
            checks = evidence['displayed_vs_expected']
            if not (ok and checks['material_source_line_match'] and checks['brief_sha256_match']):
                evidence['verdict'], evidence['reason'] = 'fail', 'displayed values differ from the fixed expected content; not acknowledged'
                return EXIT_FAIL

            # ---- explicit acknowledgement, finalize, run
            evidence['stage'] = 'acknowledge and finalize'
            ui.ack_checkbox().click()
            ui.finalize_button().click()
            ui.finalized_caption().wait_for(state='visible', timeout=60000)
            evidence['acknowledged'] = evidence['finalized'] = True
            ui.screenshot(shots, '05-finalized.png')
            evidence['stage'] = 'run and poll'
            ui.run_button().click()
            ui.terminal_state().wait_for(state='visible', timeout=900000)
            job_id, job_sha = ui.job_caption_text() or (None, None)
            if job_id is None:
                evidence['verdict'], evidence['reason'] = 'fail', 'no job caption after the run'
                return EXIT_FAIL
            job = api.json('GET', '/jobs/' + job_id)
            evidence['run'] = dict(job_id=job_id, lifecycle=job['lifecycle'], outcome=job['outcome'],
                                   brief_sha256=job['brief_sha256'], delivery=job.get('delivery'))
            if job['outcome'] != 'accepted' or job.get('delivery') != 'available':
                evidence['verdict'], evidence['reason'] = 'fail', 'run was not accepted with delivery available'
                return EXIT_FAIL
            expected_buttons = ['Download ' + FORMAT_FILES[f] for f, on in job['artifacts'].items() if on]
            for name in expected_buttons:
                page.get_by_role('button', name=name, exact=True).wait_for(state='visible', timeout=180000)
            ui.screenshot(shots, '06-result-and-downloads.png')

            # ---- downloads through the UI vs the API
            evidence['stage'] = 'downloads'
            compared = {}
            for fmt, on in job['artifacts'].items():
                if not on:
                    continue
                name = FORMAT_FILES[fmt]
                with page.expect_download() as info:
                    page.get_by_role('button', name='Download ' + name, exact=True).click()
                path = downloads / name
                info.value.save_as(str(path))
                ui_sha = sha256_bytes(path.read_bytes())
                code, headers, body = api.request('GET', f'/jobs/{job_id}/artifacts/{fmt}')
                header_sha = {k.lower(): v for k, v in headers.items()}.get('x-content-sha256')
                compared[name] = dict(ui_sha256=ui_sha, api_sha256=sha256_bytes(body), api_header_sha256=header_sha,
                                      match=code == 200 and ui_sha == sha256_bytes(body) == header_sha)
            evidence['downloads'] = compared
            samples['after_run'] = counter.read()
            if not compared or not all(v['match'] for v in compared.values()):
                evidence['verdict'], evidence['reason'] = 'fail', 'a UI download differs from the API bytes'
                return EXIT_FAIL

            # ---- same typed request: identities and selection
            evidence['stage'] = 'typed identity comparison'
            brief = api.json('POST', '/briefs', dict(
                draft_id=typed['draft']['draft_id'], draft_revision=typed['draft']['revision'],
                brief_sha256=typed['brief_sha256'],
                acknowledged_fields=api.json('GET', '/capabilities')['review_fields'],
                reviewed_at=datetime.now(timezone.utc).isoformat(), reviewed_by='b13 step 12 typed comparison'))
            typed_job = api.json('POST', '/jobs', dict(brief_id=brief['brief_id'],
                                                       idempotency_key='b13-step12-typed:' + brief['brief_id']))
            deadline = time.monotonic() + 900
            while typed_job['lifecycle'] not in ('completed', 'unavailable', 'cancelled') and time.monotonic() < deadline:
                time.sleep(3)
                typed_job = api.json('GET', '/jobs/' + typed_job['job_id'])

            def identity(j):
                return dict(brief_sha256=j['brief_sha256'], outcome=j['outcome'],
                            selected_candidate_sha256=j.get('selected_candidate_sha256'),
                            attempts=[[a['candidate']['specification']['height_mm'], a['outcome']] for a in j['attempts']])
            ai_id, typed_job_id = identity(job), identity(typed_job)
            evidence['identity_vs_typed'] = dict(ai=ai_id, typed=typed_job_id, match=ai_id == typed_job_id)
            samples['final'] = counter.read()
            if ai_id != typed_job_id:
                evidence['verdict'], evidence['reason'] = 'fail', 'semantic identity or selection differs from the typed request'
                return EXIT_FAIL
            evidence['stage'] = 'done'
            return None   # decided by the budget check
        finally:
            browser.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--compose', required=True, help='docker compose command prefix (no secrets)')
    ap.add_argument('--ui-url', default='http://127.0.0.1:8501')
    ap.add_argument('--backend-url', default='http://127.0.0.1:8000')
    ap.add_argument('--case-id', default='C01')
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--authorization', required=True)
    ap.add_argument('--source-sha', required=True)
    ap.add_argument('--release-evidence', default='release-evidence/release-images.json')
    ap.add_argument('--corpus', default='backend/tests/fixtures/engineering/ai-corpus-v1.json')
    ap.add_argument('--evidence-dir', required=True)
    ap.add_argument('--download-dir', required=True)
    ap.add_argument('--chrome', default='/usr/bin/google-chrome')
    ap.add_argument('--rehearsal', action='store_true',
                    help='provider-free: AI flag off, the typed draft of the same case replaces the proposal')
    ap.add_argument('--preflight-only', action='store_true',
                    help='prove the selectors up to the proposal button, spend nothing, write preflight.json')
    args = ap.parse_args(argv)
    if args.rehearsal and args.preflight_only:
        ap.error('--rehearsal and --preflight-only are exclusive')

    token = os.environ.get('MIRUM_ACCESS_TOKEN', '')
    if len(token) < 32:
        print('MIRUM_ACCESS_TOKEN missing', file=sys.stderr)
        return EXIT_FAIL
    secrets = [token, os.environ.get('GEMINI_API_KEY_HEAVY', '')]
    Path(args.evidence_dir).mkdir(parents=True, exist_ok=True)
    Path(args.download_dir).mkdir(parents=True, exist_ok=True)
    case = load_case(args.corpus, args.case_id)

    images = {}
    try:
        images = json.loads(Path(args.release_evidence).read_text()).get('images', {})
    except (OSError, ValueError):
        pass
    mode = 'rehearsal' if args.rehearsal else 'preflight' if args.preflight_only else 'live'
    evidence = dict(step='b13-step12', mode=mode, run_id=args.run_id, authorization=args.authorization,
                    case_id=case['id'], corpus_sha256=CORPUS_SHA256, source_sha=args.source_sha, image_ids=images,
                    budget=dict(max_total_calls=MAX_TOTAL_CALLS, max_proposal_calls=MAX_PROPOSAL_CALLS, proposal_requests_allowed=1),
                    calls={}, proposal_requests=0, verdict='error', stage='start')
    counter, api, access_log = Counter(args.compose), Api(args.backend_url, token), AccessLog(args.compose)
    code = EXIT_FAIL
    try:
        capabilities = api.json('GET', '/capabilities')
        evidence['ai_enabled'], evidence['ai_available'] = capabilities['ai_enabled'], capabilities['ai_available']
        if args.rehearsal:
            if capabilities['ai_enabled'] or capabilities['ai_available']:
                evidence['verdict'], evidence['reason'] = 'fail', 'rehearsal stack has AI enabled; refusing'
            else:
                code = run_browser(args, case, token, evidence, counter, api)
        elif not (capabilities['ai_enabled'] and capabilities['ai_available']):
            evidence['verdict'], evidence['reason'] = 'fail', 'AI drafts not enabled or credential not available; nothing spent'
        else:
            code = run_browser(args, case, token, evidence, counter, api)
        if code is None:
            problems = check_budget(evidence['calls'])
            evidence['budget_problems'] = problems
            evidence['verdict'] = 'pass' if not problems else 'fail'
            if problems:
                evidence['reason'] = 'call budget violated'
            code = EXIT_PASS if not problems else EXIT_FAIL
    except BaseException as error:   # evidence first, always. Only the type: DOM text must never reach evidence.
        evidence['error'] = f'{type(error).__name__}: driver stopped (details withheld)'
        code = EXIT_FAIL
    if not args.preflight_only:
        try:   # last ledger reading, checked on every path (L1)
            evidence['calls'].setdefault('at_exit', counter.read())
        except Exception:
            evidence['calls'].setdefault('at_exit', None)
        evidence['at_exit_budget_problems'] = check_at_exit(evidence['calls'], evidence['calls'].get('at_exit'))
        try:   # access-log containment of un-ledgered provider routes (M1)
            counts = access_log.paths()
            evidence['access_log_paths'] = counts
            evidence['access_log_problems'] = check_access_log(counts)
        except Exception:
            evidence['access_log_problems'] = ['backend access log unreadable']
        extra = evidence['at_exit_budget_problems'] + evidence['access_log_problems']
        if extra and code in (EXIT_PASS, EXIT_UNAVAILABLE):
            evidence['verdict'], evidence['reason'], code = 'fail', 'budget or access-log containment violated at exit', EXIT_FAIL
    evidence['exit_code'] = code
    evidence = scrub(evidence, secrets)
    assert_no_raw_reply(evidence)
    text = json.dumps(evidence, indent=2, sort_keys=True, default=str)
    assert_clean(text, secrets)
    name = 'preflight.json' if args.preflight_only else 'evidence.json'
    (Path(args.evidence_dir) / name).write_text(text + '\n')
    print(json.dumps({k: evidence.get(k) for k in ('mode', 'verdict', 'reason', 'stage', 'proposal_status', 'calls',
                                                    'access_log_problems', 'exit_code')}, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
