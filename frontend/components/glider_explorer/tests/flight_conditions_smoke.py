"""Optional browser integration test against a running updated API and UI."""
import os
import json
import zipfile
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
        args=['--no-sandbox', '--enable-unsafe-swiftshader'])
    page = browser.new_page(viewport={'width':1450, 'height':1200})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(os.environ.get('MIRUM_UI_URL', 'http://127.0.0.1:8501'))
    page.get_by_text('Glider Search', exact=True).click()
    page.get_by_text('Explore one design', exact=True).click()
    frame = page.frame_locator('iframe').last
    frame.locator('#status').filter(has_text='Canonical preview').wait_for(timeout=20000)
    mode = frame.locator('[data-field="flight.mode"]')
    speed = frame.locator('[data-field="flight.airspeed_m_s"]')
    assert speed.is_disabled()
    mode.select_option('specified')
    for field, value in [('airspeed_m_s','7'), ('alpha_deg','6'), ('beta_deg','3'), ('altitude_m','1500')]:
        control = frame.locator('[data-field="flight.'+field+'"]')
        control.fill(value)
        control.press('Tab')
    frame.get_by_role('button', name='Recalculate performance', exact=True).click()
    frame.locator('#status').filter(has_text='Specified-condition analysis complete').wait_for(timeout=60000)
    metrics = frame.locator('#metrics').inner_text()
    assert 'Lift (N)' in metrics and 'Drag (N)' in metrics and 'not trimmed' in metrics
    assert '1500 m' in metrics and '7 m/s' in metrics
    assert 'Still-air distance (m)' not in metrics
    with page.expect_download() as download:
        frame.get_by_role('button',name='Download evaluated revision',exact=True).click()
    with zipfile.ZipFile(download.value.path()) as archive:
        data = json.loads(archive.read('design.json'))
        assert data['analysis_mode'] == 'specified'
        assert data['operating_conditions']['altitude_m'] == 1500
        assert data['operating_conditions']['airspeed_m_s'] == 7
        assert data['metrics']['lift_N'] > 0
    speed.fill('20')
    speed.press('Tab')
    assert not speed.evaluate('(e)=>e.checkValidity()')
    assert not frame.locator('#metrics').inner_text()
    speed.fill('5')
    speed.press('Tab')
    mode.select_option('automatic_trim')
    assert speed.is_disabled()
    frame.get_by_role('button',name='Recalculate performance',exact=True).click()
    frame.locator('#status').filter(has_text='Performance is current').wait_for(timeout=60000)
    assert 'Still-air distance (m)' in frame.locator('#metrics').inner_text()
    frame.get_by_role('button', name='Reset design',exact=True).click()
    assert mode.input_value() == 'automatic_trim'
    assert speed.input_value() == '5'
    assert not errors, errors
    page.screenshot(path='/tmp/glider-flight-conditions.png',full_page=True)
    print('Flight controls, specified forces/export, validation, automatic trim and reset passed')
    browser.close()
