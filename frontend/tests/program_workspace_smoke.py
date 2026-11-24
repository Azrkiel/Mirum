"""Playwright smoke against a running Mirum frontend and native local backend."""

import argparse
import json
from pathlib import Path
import re
import time

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


def settle(page, state):
    expected = page.get_by_text(f'Revision state: {state}', exact=True)
    stop = time.monotonic() + 165
    while time.monotonic() < stop:
        if expected.is_visible():
            return
        if state != 'failed' and page.get_by_text('Revision state: failed', exact=True).is_visible():
            raise AssertionError('revision failed before reaching the expected state')
        action(page, 'Refresh revision status')
        page.wait_for_timeout(1200)
    raise AssertionError(f'revision did not reach {state}')


def resume(page, key):
    entry = page.get_by_role('textbox', name='Resume program ID')
    entry.fill(key)
    entry.press('Tab')
    action(page, 'Open program')
    expect(page.get_by_text(f'Program ID {key}', exact=True)).to_be_visible(timeout=30_000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8528')
    parser.add_argument('--browser', default='/usr/bin/chromium')
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.browser, args=['--no-sandbox'])
        page = browser.new_page()
        try:
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
            expect(page.get_by_role('button', name='Download generator-body.step')).to_be_visible()
            action(other, 'Refresh revision status')
            expect(other.get_by_text(re.compile('The accepted head changed'))).to_be_visible(timeout=30_000)
            expect(other.get_by_role('button', name='Stage source')).to_be_disabled()
            action(page, 'Load selected revision source')
            # Two revisions here share the same fixed requirements.
            source = textarea.input_value()
            textarea.fill(source.replace('"nominal"', '"supplier-shifted"'))
            textarea.press('Tab')
            action(page, 'Stage source')
            review(page)
            settle(page, 'rejected')
            action(page, 'Read rejection report')
            expect(page.get_by_text('Revision evidence', exact=True)).to_be_visible()
            action(page, 'Compare saved revisions')

            page.reload()
            open_editor(page, args.url)
            resume(page, key)
            expect(page.get_by_role('button', name='Load selected revision source')).to_be_visible()
            if args.screenshot:
                page.screenshot(path=args.screenshot)
            print(json.dumps({'browser': browser.version, 'bay_nominal': 'published', 'substitution': 'rejected',
                              'diagnostic': 'MEL-P002', 'stale_tab_blocked': True, 'resume_after_reload': True}))
        finally:
            browser.close()


if __name__ == '__main__':
    main()
