/* Real browser/native UI qualification. No provider credentials or oracle calls. */
const fs=require('fs');
const path=require('path');
const crypto=require('crypto');
const assert=require('assert');
const {spawnSync}=require('child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const out=process.argv[2]; const sha=process.argv[3];
const frontend=process.env.CHECKED_UI_URL || 'http://127.0.0.1:18501/';
const backend=process.env.CHECKED_API_URL || 'http://127.0.0.1:18765/engineering/v1';
const ack='I reviewed every value, the normalized thickness set, material source, ideal clamp, +Z end force and all assumptions.';
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
fs.mkdirSync(out,{recursive:true});

function fault(code,...args){
  const container=process.env.CHECKED_CONTAINER;
  assert(container && /^mirum-checked-[a-z0-9-]+$/.test(container),'Set the owned CHECKED_CONTAINER for real write-failure/expiry checks');
  const result=spawnSync('docker',['exec',container,'python','-c',code,...args],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
}

async function run(){
  const browser=await chromium.launch({headless:true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE}:{}),
    args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
  context.setDefaultTimeout(60000);
  const observations=[]; const errors=[];
  let ready=false;
  for(let n=0;n<120;n++){
    try{
      const a=await context.request.get(frontend+'_stcore/health',{timeout:1000});
      const b=await context.request.get(backend.replace('/engineering/v1','')+'/openapi.json',{timeout:1000});
      if(a.ok()&&b.ok()){ready=true;break;}
    }catch{}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  assert(ready,'Local UI/API did not start');
  await context.route('**/*',route=>{
    const host=new URL(route.request().url()).hostname;
    return ['127.0.0.1','localhost'].includes(host)?route.continue():route.abort();
  });
  context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
  const fresh=async()=>{const page=await context.newPage(); await page.goto(frontend+'?checked_workflow=1');await page.getByRole('button',{name:'Save draft',exact:true}).waitFor();return page;};
  const review=async page=>{
    await page.getByRole('button',{name:'Save draft',exact:true}).click();
    await page.getByText(ack,{exact:true}).click();
    await page.getByRole('button',{name:'Finalize reviewed brief',exact:true}).click();
    await page.getByRole('button',{name:'Run reviewed beam checks',exact:true}).click();
    await page.getByRole('button',{name:'Cancel current job',exact:true}).waitFor({timeout:30000});
    return new URL(page.url()).searchParams.get('checked_job');
  };
  try{
    const page=await fresh();
    const jobId=await review(page); assert(jobId);
    const during=page.url(); await page.reload();
    await page.getByText('Accepted under the declared digital beam model.',{exact:false}).waitFor({timeout:120000});
    await page.getByRole('button',{name:'Download package.zip',exact:true}).waitFor({timeout:60000});
    assert.equal(new URL(page.url()).searchParams.get('checked_job'),jobId);
    const chart=page.locator('.js-plotly-plot'); await chart.waitFor();
    const mesh=await chart.evaluate(el=>({vertices:el.data[0].x.length,triangles:el.data[0].i.length,names:el.data.map(t=>t.name)}));
    assert(mesh.vertices>=8 && mesh.triangles===12);
    await chart.screenshot({path:path.join(out,'accepted-preview.png')});
    const response=await context.request.get(backend+'/jobs/'+jobId); assert(response.ok()); const job=await response.json();
    assert.deepEqual(job.attempts.map(a=>a.candidate.specification.height_mm),[5,10,15]);
    assert.equal(job.delivery,'available');
    const files={}; const folder=path.join(out,'downloads'); fs.mkdirSync(folder,{recursive:true});
    const expected=Object.fromEntries(job.bundle.payloads.map(p=>[p.path,p.sha256]));
    expected['manifest.json']=job.bundle.manifest_sha256; expected['package.zip']=job.bundle.zip_sha256;
    for(const name of Object.keys(expected)){
      const event=page.waitForEvent('download');
      await page.getByRole('button',{name:'Download '+name,exact:true}).click();
      const download=await event; assert.equal(download.suggestedFilename(),name);
      const filename=path.join(folder,name); await download.saveAs(filename);
      const bytes=fs.readFileSync(filename); assert.equal(hash(bytes),expected[name]);
      files[name]={bytes:bytes.length,sha256:hash(bytes)};
    }
    await page.getByText('Read report',{exact:true}).click();
    await page.getByText('Digital beam-model evidence, not physical testing, manufacturing release or standards certification.',{exact:false}).waitFor();
    await page.getByRole('heading',{name:'Revision-bound downloads',exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(out,'downloads-and-report.png')});
    observations.push({case:'accepted_reference',job_id:jobId,url_during_job:during,url_after_refresh:page.url(),mesh,files});
    // Editing invalidates review while the previous result remains bound to its job.
    await page.getByRole('spinbutton',{name:'Tip force (+Z, N)',exact:true}).fill('60');
    await page.getByRole('button',{name:'Save draft',exact:true}).click();
    await page.getByText('This result belongs to the earlier reviewed design.',{exact:false}).waitFor();
    assert(await page.getByRole('button',{name:'Run reviewed beam checks',exact:true}).isDisabled());
    const second=await context.newPage(); await second.goto(page.url());
    await second.getByRole('button',{name:'Save draft',exact:true}).waitFor();
    await second.getByRole('spinbutton',{name:'Tip force (+Z, N)',exact:true}).fill('55');
    await second.getByRole('button',{name:'Save draft',exact:true}).click();
    await second.getByText(/revision 3 \| brief SHA-256/).waitFor();
    await page.getByText(ack,{exact:true}).click();
    await page.getByText('This draft changed in another tab.',{exact:false}).waitFor();
    assert(await page.getByRole('button',{name:'Run reviewed beam checks',exact:true}).isDisabled());
    await page.getByRole('button',{name:'Load latest revision',exact:true}).click();
    await page.getByText(/revision 3 \| brief SHA-256/).waitFor();
    assert.equal(await page.getByRole('spinbutton',{name:'Tip force (+Z, N)',exact:true}).inputValue(),'55.00000');
    assert(await page.getByRole('button',{name:'Finalize reviewed brief',exact:true}).isDisabled());
    observations.push({case:'edit_and_two_tabs',draft_id:new URL(page.url()).searchParams.get('checked_draft'),latest_revision:3,old_job_id:jobId,unreviewed_run_disabled:true});
    await page.screenshot({path:path.join(out,'multiple-tabs.png')}); await second.close();
    // Real missing-provider transport; no fixture provider is installed.
    const ai=await fresh(); await ai.getByText('AI-assisted draft',{exact:true}).click();
    await ai.getByRole('textbox',{name:'Engineering request',exact:true}).fill('Prepare a checked cantilever.');
    await ai.getByRole('textbox',{name:'Engineering request',exact:true}).press('Tab');
    await ai.getByRole('button',{name:'Propose draft',exact:true}).click();
    await ai.getByText('A required service is unavailable. Typed preparation remains available.',{exact:false}).waitFor();
    assert(await ai.getByRole('button',{name:'Run reviewed beam checks',exact:true}).isDisabled());
    observations.push({case:'ai_unavailable',real_provider_calls:0,unreviewed_run_disabled:true}); await ai.close();
    // Unsupported preparation is seeded via real HTTP, never a provider oracle.
    const payload=JSON.parse(fs.readFileSync('backend/tests/fixtures/engineering/brief-draft.json','utf8')).content;
    const unsupported=await context.request.post(backend+'/drafts',{data:{input:{...payload,features:['holes']}}});
    assert.equal(unsupported.status(),201); const unsupportedDraft=await unsupported.json();
    const blocked=await context.newPage(); await blocked.goto(frontend+'?checked_workflow=1&checked_draft='+unsupportedDraft.draft.draft_id);
    await blocked.getByText('Unsupported requirements cannot be removed by saving this form.',{exact:false}).waitFor();
    assert(await blocked.getByRole('button',{name:'Run reviewed beam checks',exact:true}).isDisabled());
    assert(await blocked.getByRole('button',{name:'Save draft',exact:true}).isDisabled());
    observations.push({case:'unsupported_seeded_http',feature:'holes',silent_drop:false}); await blocked.close();
    const cancelled=await fresh(); const cancelId=await review(cancelled);
    await cancelled.getByRole('button',{name:'Cancel current job',exact:true}).click();
    await cancelled.getByText('Cancelled before terminal commit.',{exact:false}).waitFor({timeout:30000});
    assert.equal(await cancelled.getByRole('button',{name:'Download package.zip',exact:true}).count(),0);
    observations.push({case:'cancelled',job_id:cancelId}); await cancelled.close();
    const rejected=await fresh(); await rejected.getByRole('spinbutton',{name:'Allowable stress (MPa)',exact:true}).fill('1');
    const rejectedId=await review(rejected);
    await rejected.getByText('No reviewed candidate passed.',{exact:false}).waitFor({timeout:120000});
    await rejected.getByRole('button',{name:'Download package.zip',exact:true}).waitFor({timeout:30000});
    assert.equal(await rejected.getByRole('button',{name:'Download model.step',exact:true}).count(),0);
    assert.equal(await rejected.locator('.js-plotly-plot').count(),0);
    await rejected.getByRole('heading',{name:'Checked result',exact:true}).scrollIntoViewIfNeeded();
    await rejected.screenshot({path:path.join(out,'rejected.png')});
    observations.push({case:'rejected',job_id:rejectedId,accepted_geometry:false}); await rejected.close();
    // Real OS publication failure, not an injected publisher return value.
    const unavailable=await fresh();
    fault("from pathlib import Path; Path('/tmp/mirum/engineering/bundles').chmod(0o500)");
    try{
      const unavailableId=await review(unavailable);
      await unavailable.getByText('A required execution or publication stage is unavailable.',{exact:false}).waitFor({timeout:120000});
      assert.equal(await unavailable.getByRole('button',{name:'Download package.zip',exact:true}).count(),0);
      observations.push({case:'unavailable_publication',job_id:unavailableId,fault:'Actual directory write permission denied',accepted_geometry:false});
    }finally{fault("from pathlib import Path; Path('/tmp/mirum/engineering/bundles').chmod(0o700)");await unavailable.close();}
    // Accelerate the persisted expiry timestamp; do not claim six hours elapsed.
    fault("import sqlite3,sys,time; d=sqlite3.connect('/tmp/mirum/engineering/engineering.sqlite3'); d.execute('UPDATE jobs SET expires=? WHERE id=?',(time.time()-1,sys.argv[1])); d.execute('UPDATE publications SET expires=? WHERE job=?',(time.time()-1,sys.argv[1])); d.commit()",jobId);
    await page.getByText('Expired. The six-hour download window ended.',{exact:false}).waitFor({timeout:30000});
    for(const name of Object.keys(expected)){
      const button=page.getByRole('button',{name:'Download '+name,exact:true});
      await button.waitFor({state:'detached',timeout:10000});
      assert.equal(await button.count(),0);
    }
    assert.equal((await context.request.get(backend+'/jobs/'+jobId+'/artifacts/zip')).status(),410);
    observations.push({case:'expired',job_id:jobId,clock:'Persisted timestamp accelerated; not a six-hour wall-clock wait',downloads_withheld:true});
    await page.getByText('Expired. The six-hour download window ended.',{exact:false}).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(out,'expired.png')});
    assert.deepEqual(errors,[]);
    const evidence={tested_implementation_sha:sha,browser:await browser.version(),playwright:require(path.join(path.dirname(require.resolve(process.env.PLAYWRIGHT_MODULE||'playwright')),'package.json')).version,
      provider_calls:0,network:'Browser external requests blocked; local HTTP only',scope:'Real browser/HTTP/native with explicit user-like review clicks; unsupported input seeded via HTTP',observations,page_errors:errors};
    fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(evidence,null,2)+'\n');
    console.log(JSON.stringify({sha,browser:evidence.browser,cases:observations.map(o=>o.case),page_errors:errors}));
  }catch(error){
    let index=0;
    for(const page of context.pages()){
      try{
        fs.writeFileSync(path.join(out,'failure-'+index+'.txt'),await page.locator('body').innerText());
        await page.screenshot({path:path.join(out,'failure-'+index+'.png'),timeout:10000});
      }catch{}
      index++;
    }
    throw error;
  }finally{await context.close();await browser.close();}
}
run().catch(error=>{console.error(error);process.exitCode=1;});
