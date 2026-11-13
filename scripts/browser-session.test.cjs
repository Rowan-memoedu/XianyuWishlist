const test=require('node:test');
const assert=require('node:assert/strict');
const {readBrowserSession}=require('./browser-session.cjs');

function fixture(cookies,pageUrl='about:blank'){
  const calls=[];
  const page={url:()=>pageUrl,close:async()=>calls.push('close-owned-blank')};
  const context={pages:()=>[page],cookies:async urls=>{calls.push(urls);return cookies;}};
  const browser={contexts:()=>[context],close:async()=>calls.push('disconnect')};
  const tools={resolveCLIConfigForMCP:async options=>{calls.push(options);return options;},
    createBrowserWithInfo:async()=>({browser})};
  return {tools,available:()=>true,credential:'synthetic-credential',calls};
}
test('reads only Goofish cookies via the official extension and disconnects cleanly',async()=>{
  const f=fixture([{name:'unb',value:'synthetic',domain:'.goofish.com'},
    {name:'XSRF-TOKEN',value:'synthetic',domain:'passport.goofish.com'},
    {name:'private',value:'must-not-return',domain:'.unrelated.example'}]);
  const result=await readBrowserSession(f);
  assert.equal(result.length,2);
  assert.deepEqual(f.calls[1],['https://www.goofish.com/','https://passport.goofish.com/','https://h5api.m.goofish.com/']);
  assert.deepEqual(f.calls.slice(-2),['close-owned-blank','disconnect']);
});
test('never launches a closed browser or falls back to a different profile',async()=>{
  const f=fixture([]);f.available=()=>false;
  await assert.rejects(readBrowserSession(f),/browser_unavailable/);
  assert.equal(f.calls.length,0);
});
test('does not close a tab the user navigated and does not export a logged-out session',async()=>{
  const f=fixture([{name:'t',value:'tracking',domain:'.goofish.com'}],'https://www.goofish.com/');
  await assert.rejects(readBrowserSession(f),/browser_not_logged_in/);
  assert.equal(f.calls.includes('close-owned-blank'),false);
  assert.equal(f.calls.at(-1),'disconnect');
});
