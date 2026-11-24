"""Playwright smoke against a running Mirum frontend and native local backend."""

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def open_editor(page, url):
    page.goto(url)
    page.get_by_text('Checked design', exact=True).first.click()
    page.get_by_text('MEL program', exact=True).first.click()
    expect(page.get_by_role('button', name='Create program')).to_be_visible(timeout=30_000)


def action(page, name):
    page.get_by_role('button', name=name, exact=True).click()


def review(page):
    page.get_by_text('I reviewed the listed fields, assumptions and candidate choices.', exact=True).click()
    reviewer = page.get_by_role('textbox', name='Reviewer name')
    reviewer.fill('browser smoke')
    reviewer.press('Tab')
    action(page, 'Record review')
    expect(page.get_by_text('Review recorded', exact=True)).to_be_visible(timeout=30_000)
    action(page, 'Run reviewed revision')
    expect(page.get_by_text('Revision state: staged', exact=True)).not_to_be_visible(timeout=30_000)


def settle(page, state, timeout=165):
    expected = page.get_by_text(f'Revision state: {state}', exact=True)
    stop = time.monotonic() + timeout
    while time.monotonic() < stop:
        if expected.is_visible():
            return
        if state != 'failed' and page.get_by_text('Revision state: failed', exact=True).is_visible():
            raise AssertionError('revision failed before reaching the expected state')
        action(page, 'Refresh revision status')
        page.wait_for_timeout(1200)
    raise AssertionError(f'revision did not reach {state}: {page.get_by_text(re.compile("^Revision state:")).all_text_contents()}')


def resume(page, key):
    entry = page.get_by_role('textbox', name='Resume program ID')
    entry.fill(key)
    entry.press('Tab')
    action(page, 'Open program')
    expect(page.get_by_text(f'Program ID {key}', exact=True)).to_be_visible(timeout=30_000)


def revision_control(page):
    return page.get_by_test_id('stSelectbox').filter(has=page.get_by_text('Revision', exact=True))


def select_revision(page, prefix):
    control = revision_control(page)
    control.locator('[role="combobox"]').click()
    page.get_by_role('option', name=prefix, exact=True).click()
    expect(control.get_by_text(prefix, exact=True)).to_be_visible()


def expire_test_publication(database, program, prefix):
    with sqlite3.connect(database.as_uri() + '?mode=rw', uri=True) as db:
        rows = db.execute('SELECT job_id FROM program_revisions WHERE program_id=? AND id LIKE ? '
                          "AND state='published' AND kind='edit'", (program, prefix + '%')).fetchall()
        assert len(rows) == 1 and rows[0][0]
        changed = db.execute('UPDATE publications SET expires=? WHERE job=?', (time.time() - 1, rows[0][0]))
        assert changed.rowcount == 1


def beam_flow(page, url, fixture_db):
    open_editor(page, url)
    action(page, 'Create program')
    program = page.get_by_text(re.compile(r'^Program ID [a-f0-9]{32}$')).text_content().split()[-1]
    action(page, 'Stage source')
    expect(page.get_by_text('Revision state: staged', exact=True)).to_be_visible(timeout=30_000)
    review(page)
    settle(page, 'published')
    first = revision_control(page).get_by_text(re.compile(r'^[a-f0-9]{12}$')).text_content()
    expect(page.locator('iframe').first.content_frame.locator('#renderCanvas')).to_be_visible(timeout=20_000)

    page.get_by_text('AI-assisted beam proposal', exact=True).click()
    prompt = page.get_by_role('textbox', name='Describe a beam revision')
    prompt.fill('Try the same reviewed beam with a single candidate.')
    prompt.press('Tab')
    action(page, 'Request AI draft')
    expect(page.get_by_text('Program request failed.', exact=True)).to_be_visible(timeout=30_000)
    action(page, 'Load selected revision source')
    expect(page.get_by_role('button', name='Stage source')).to_be_enabled()
    source_field = page.get_by_role('textbox', name='MEL source')
    source_field.fill(source_field.input_value().replace('[5 mm, 10 mm, 15 mm]', '[20 mm]'))
    source_field.press('Tab')
    action(page, 'Stage source')
    action(page, 'Cancel revision')
    settle(page, 'cancelled')
    action(page, 'Stage source')
    review(page)
    settle(page, 'published')
    second = revision_control(page).get_by_text(re.compile(r'^[a-f0-9]{12}$')).text_content()
    assert first != second
    select_revision(page, first)
    comparison = page.get_by_test_id('stSelectbox').filter(has=page.get_by_text('Compare with revision', exact=True))
    comparison.locator('[role="combobox"]').click()
    page.get_by_role('option', name=second, exact=True).click()
    action(page, 'Compare saved revisions')
    expect(page.get_by_text('Saved revision comparison', exact=True)).to_be_visible()

    page.get_by_text('Restore this saved revision as the accepted head.', exact=True).click()
    action(page, 'Restore selected revision')
    expect(page.get_by_text('Accepted head', exact=True)).to_be_visible(timeout=30_000)
    if fixture_db:
        select_revision(page, second)
        expect(page.get_by_text('Download preview.glb', exact=True)).to_be_visible()
        expire_test_publication(fixture_db, program, second)
        action(page, 'Refresh revision status')
        expect(page.get_by_text('This revision’s package expired. Its evidence history is still available.',
                                exact=True)).to_be_visible()
        expect(page.get_by_text('Download preview.glb', exact=True)).not_to_be_visible()
        expect(page.get_by_role('button', name='Restore selected revision')).not_to_be_visible()



def form_flow(page, url):
    page.goto(url)
    page.get_by_text('Checked design', exact=True).first.click()
    action(page, 'Save draft')
    expect(page.get_by_text('2. Review the exact draft', exact=True)).to_be_visible(timeout=30_000)
    page.get_by_text('I reviewed every value, the normalized thickness set, material source, '
                     'ideal clamp, +Z end force and all assumptions.', exact=True).click()
    action(page, 'Finalize reviewed brief')
    action(page, 'Run reviewed beam checks')
    expect(page.get_by_text(re.compile('Accepted under the declared digital beam model'))).to_be_visible(
        timeout=120_000)
    expect(page.get_by_text('Revision-bound downloads', exact=True)).to_be_visible()


def pico_flow(browser, page, url):
    open_editor(page, url)
    action(page, 'Load PicoGK example')
    expect(page.get_by_role('textbox', name='MEL source')).to_have_value(re.compile('design picogk_reference'))
    action(page, 'Create program')
    program = page.get_by_text(re.compile(r'^Program ID [a-f0-9]{32}$')).text_content().split()[-1]
    action(page, 'Stage source')
    expect(page.get_by_text('Revision state: staged', exact=True)).to_be_visible()
    other = browser.new_page()
    open_editor(other, url)
    resume(other, program)
    action(other, 'Load selected revision source')
    expect(other.get_by_text(re.compile(r'specification\.voxel_size_mm'))).to_be_visible()
    review(other)
    settle(other, 'published', timeout=420)
    expect(other.get_by_text('Download generator-report.json', exact=True)).to_be_visible()
    expect(other.get_by_text('AI-assisted beam proposal', exact=True)).not_to_be_visible()
    artifacts = other.get_by_test_id('stSelectbox').filter(has=other.get_by_text('Revision artifact', exact=True))
    artifacts.locator('[role="combobox"]').click()
    other.get_by_role('option', name='generator-cleaned.stl', exact=True).click()
    download_button = other.get_by_text('Download generator-cleaned.stl', exact=True)
    expect(download_button).to_be_visible(timeout=90_000)
    with other.expect_download(timeout=90_000) as download:
        download_button.click()
    assert download.value.suggested_filename == 'generator-cleaned.stl'
    assert download.value.path().stat().st_size > 64 * 1024**2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8528')
    parser.add_argument('--browser', default='/usr/bin/chromium')
    parser.add_argument('--screenshot', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--beam', action='store_true', help='exercise captured GLB, unavailable AI, cancel and restore')
    mode.add_argument('--form', action='store_true', help='run the existing checked form with the provider unavailable')
    mode.add_argument('--pico', action='store_true', help='resume, review, and export the native PicoGK preset')
    parser.add_argument('--local-fixture-db', type=Path, help='exercise expiry using a disposable local backend database')
    args = parser.parse_args()
    if args.local_fixture_db and (not args.beam or urlsplit(args.url).hostname not in ('127.0.0.1', 'localhost')):
        parser.error('--local-fixture-db requires --beam and a loopback frontend')
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.browser, args=['--no-sandbox'])
        page = browser.new_page()
        try:
            if args.pico:
                pico_flow(browser, page, args.url)
                print(json.dumps({'browser': browser.version, 'picogk': 'published', 'stl': 'available'}))
                return
            if args.form:
                form_flow(page, args.url)
                print(json.dumps({'browser': browser.version, 'checked_form': 'accepted'}))
                return
            if args.beam:
                beam_flow(page, args.url, args.local_fixture_db)
                print(json.dumps({'browser': browser.version, 'preview': 'saved GLB',
                                  'provider': 'unavailable', 'cancel': 'passed', 'restore': 'passed',
                                  'expired': 'passed' if args.local_fixture_db else 'not_run'}))
                return
            open_editor(page, args.url)
            textarea = page.get_by_role('textbox', name='MEL source')
            textarea.fill('invalid source')
            textarea.press('Tab')
            action(page, 'Validate and preview plan')
            expect(page.get_by_text('MEL-P002', exact=True)).to_be_visible(timeout=30_000)
            action(page, 'Load equipment-bay example')
            expect(textarea).to_have_value(re.compile('design equipment_bay'))
            action(page, 'Create program')
            program = page.get_by_text(re.compile(r'^Program ID [a-f0-9]{32}$'))
            expect(program).to_be_visible(timeout=30_000)
            key = program.text_content().split()[-1]
            other = browser.new_page()
            open_editor(other, args.url)
            resume(other, key)
            action(page, 'Stage source')
            expect(page.get_by_text('Revision state: staged', exact=True)).to_be_visible(timeout=30_000)
            review(page)
            settle(page, 'published')
            print('nominal browser revision published', file=sys.stderr, flush=True)
            expect(page.get_by_text('Download generator-body.step', exact=True)).to_be_visible()
            action(other, 'Refresh revision status')
            expect(other.get_by_text(re.compile('The accepted head changed'))).to_be_visible(timeout=30_000)
            expect(other.get_by_role('button', name='Stage source')).to_be_disabled()
            action(page, 'Load selected revision source')
            expect(page.get_by_role('button', name='Stage source')).to_be_enabled()
            # Two revisions here share the same fixed requirements.
            source = textarea.input_value()
            textarea.fill(source.replace('"nominal"', '"supplier-shifted"'))
            textarea.press('Tab')
            action(page, 'Stage source')
            review(page)
            settle(page, 'rejected')
            print('substitution browser revision rejected', file=sys.stderr, flush=True)
            action(page, 'Read rejection report')
            expect(page.get_by_text('Saved rejection report', exact=True)).to_be_visible()
            expect(page.get_by_role('cell', name='module-01', exact=True)).to_have_count(4)
            action(page, 'Compare saved revisions')
            expect(page.get_by_text('Saved revision comparison', exact=True)).to_be_visible()

            page.reload()
            open_editor(page, args.url)
            resume(page, key)
            expect(page.get_by_role('button', name='Load selected revision source')).to_be_visible()
            if args.screenshot:
                page.screenshot(path=args.screenshot)
            print(json.dumps({'browser': browser.version, 'bay_nominal': 'published', 'substitution': 'rejected',
                              'diagnostic': 'MEL-P002', 'stale_tab_blocked': True, 'resume_after_reload': True}))
        except Exception:
            if args.screenshot:
                page.screenshot(path=args.screenshot)
            raise
        finally:
            browser.close()


if __name__ == '__main__':
    main()
