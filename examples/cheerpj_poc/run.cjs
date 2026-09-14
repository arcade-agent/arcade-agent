// Uses an installed Playwright browser; fresh context per invocation.
const {chromium} = require('playwright');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
(async () => {
  const root = __dirname;
  const server = http.createServer((req,res) => {
    const pathname = new URL(req.url,'http://localhost').pathname;
    if(pathname==='/favicon.ico') {res.writeHead(204);res.end();return;}
    const file = path.resolve(root, '.' + pathname);
    if (!file.startsWith(root + path.sep)) {res.writeHead(403);res.end();return;}
    const target = file.endsWith(path.sep) ? file+'index.html' : file;
    try {
      const data=fs.readFileSync(target);
      res.setHeader('Content-Type',target.endsWith('.html')?'text/html':'application/octet-stream');
      res.setHeader('Accept-Ranges','bytes');
      const match=/^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '');
      if(match) {
        const start=Number(match[1]), end=Math.min(match[2]?Number(match[2]):data.length-1,data.length-1);
        if(start>end) {res.writeHead(416);res.end();return;}
        res.writeHead(206,{'Content-Range':`bytes ${start}-${end}/${data.length}`,'Content-Length':end-start+1});
        res.end(data.subarray(start,end+1));
      } else {res.setHeader('Content-Length',data.length);res.end(data);}
    } catch {res.writeHead(404);res.end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    const native=[];
    for(let i=0;i<3;i++) {
      const start=performance.now();
      const p=spawnSync('java',['-cp',path.join(root,'build/probe.jar'),'Probe',path.join(root,'build/MainActivity.kt')],{encoding:'utf8'});
      native.push({ms:performance.now()-start,status:p.status,stdout:p.stdout,stderr:p.stderr});
    }
    const start=performance.now();
    browser=await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || chromium.executablePath()});
    const page=await browser.newPage();
    const logs=[];let bytes=0;
    page.on('console',m=>{logs.push(m.text()); process.stderr.write(m.text()+'\n');});
    page.on('pageerror',e=>logs.push(String(e)));
    page.on('requestfailed',r=>logs.push('REQUEST_FAILED '+r.url()+' '+JSON.stringify(r.failure())));
    page.on('response',r=>{if(r.status()>=400) logs.push('HTTP '+r.status()+' '+r.url());});
    const cdp=await page.context().newCDPSession(page);
    await cdp.send('Network.enable');
    cdp.on('Network.loadingFinished',e=>bytes+=e.encodedDataLength);
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
    let timeoutError;
    try {await page.waitForFunction(()=>window.result!==undefined,{},{timeout:180000});}
    catch(e) {timeoutError=String(e);}
    const diagnostic=await page.evaluate(()=>({title:document.title, text:document.body.innerText, scripts:[...document.scripts].map(s=>s.src)}));
    console.log(JSON.stringify({timeoutError,diagnostic,native,cheerpj:await page.evaluate(()=>window.result),
      browserLaunchToResultMs:performance.now()-start,networkEncodedBytes:bytes,logs},null,2));
    const result=await page.evaluate(()=>window.result);
    if(timeoutError || native.some(r=>r.status!==0) || !result?.exits?.every(c=>c===0)
       || logs.filter(s=>s.startsWith('POC_OK')).length!==3) process.exitCode=1;
  } finally {if(browser) await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;});
