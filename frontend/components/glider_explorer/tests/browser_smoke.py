from playwright.sync_api import sync_playwright
import json
import os
import zipfile
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--enable-unsafe-swiftshader'])
    page=browser.new_page(viewport={'width':1450,'height':1200})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(os.environ.get('MIRUM_UI_URL', 'http://127.0.0.1:8501'))
    page.get_by_text('Passive glider distance search',exact=True).click()
    page.get_by_text('Explore one design',exact=True).click()
    frame=page.frame_locator('iframe[title="components.glider_explorer.glider_explorer"]')
    # Component name is reported below if Streamlit changes it.
    page.wait_for_timeout(2500)
    print('iframes',page.locator('iframe').evaluate_all('(els)=>els.map(e=>({title:e.title,src:e.src}))'))
    frame=page.frame_locator('iframe').last
    frame.locator('#status').filter(has_text='Canonical preview').wait_for(timeout=20000)
    slider=frame.locator('[data-field="wing.span"]')
    box=slider.bounding_box()
    before=frame.locator('canvas').evaluate('(e)=>window.__gliderMeshVersion')
    page.mouse.move(box['x']+box['width']*.5,box['y']+box['height']/2)
    page.mouse.down()
    page.mouse.move(box['x']+box['width']*.7,box['y']+box['height']/2,steps=12)
    page.wait_for_timeout(60)
    during=frame.locator('canvas').evaluate('(e)=>window.__gliderMeshVersion')
    assert during>before,(before,during)
    page.mouse.up()
    page.wait_for_timeout(2500)
    print('after drag status',frame.locator('#status').inner_text())
    frame.get_by_role('button',name='Front',exact=True).click()
    assert '° upward' in frame.locator('#guide').inner_text()
    frame.get_by_role('button',name='Recalculate performance',exact=True).click()
    frame.locator('#status').filter(has_text='Performance is current').wait_for(timeout=60000)
    print('status',frame.locator('#status').inner_text())
    print('metrics',frame.locator('#metrics').inner_text())
    print('errors',errors)
    with page.expect_download() as download:
        frame.get_by_role('button',name='Download evaluated revision',exact=True).click()
    with zipfile.ZipFile(download.value.path()) as z:
        report=json.loads(z.read('design.json'))
        assert report['geometry']['geometry_digest']
        assert len(z.read('assembly.stl'))>84
    # A result for an older edit must not overwrite the current geometry/metrics.
    frame.get_by_role('button',name='Recalculate performance',exact=True).click()
    slider.evaluate("e=>{e.value='1.252';e.dispatchEvent(new Event('input',{bubbles:true}));}")
    slider.dispatch_event('change')
    page.wait_for_timeout(6000)
    assert not frame.locator('#metrics').inner_text()
    assert 'Performance needs recalculation' in frame.locator('#status').inner_text()
    frame.get_by_role('button',name='Compare original',exact=True).click()
    frame.get_by_role('button',name='Reset design',exact=True).click()
    assert float(slider.input_value()) == 1.1
    page.screenshot(path='/tmp/glider-explorer.png',full_page=True)
    assert not errors
    browser.close()
