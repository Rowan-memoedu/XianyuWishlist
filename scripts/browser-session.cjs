// Domain-scoped reads through the user's preinstalled official Playwright extension.
const {execFileSync}=require('node:child_process');
const URLS=['https://www.goofish.com/','https://passport.goofish.com/','https://h5api.m.goofish.com/'];
const scoped=c=>typeof c?.domain==='string'&&(c.domain.replace(/^\./,'')==='goofish.com'||c.domain.endsWith('.goofish.com'));
function chromeRunning(){
  if(process.platform!=='win32')return false;
  try{
    const command="@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object {$_.CommandLine -notmatch '--type=' -and $_.CommandLine -notmatch '--user-data-dir'}).Count";
    return Number(execFileSync('pwsh',['-NoLogo','-NoProfile','-Command',command],{windowsHide:true,timeout:10000,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim())>0;
  }catch{return false;}
}
async function readBrowserSession({tools,available=chromeRunning,credential=process.env.PLAYWRIGHT_MCP_EXTENSION_TOKEN}={}){
  if(!credential)throw Error('browser_credential_missing');
  if(!available())throw Error('browser_unavailable');
  tools??=require('playwright-core/lib/coreBundle').tools;
  let browser,page;
  try{
    const options={browser:'chrome',extension:true,profileDirName:'Default'};
    const config=await tools.resolveCLIConfigForMCP(options);
    ({browser}=await tools.createBrowserWithInfo(config,{clientName:'Xianyu session read-only'},options));
    const context=browser.contexts()[0];
    page=context.pages()[0];
    const cookies=(await context.cookies(URLS)).filter(scoped);
    if(!cookies.some(c=>c.name==='unb'&&c.value))throw Error('browser_not_logged_in');
    return cookies;
  }finally{
    // The token-authenticated client owns a new blank tab; leave any navigated tab alone.
    if(page?.url()==='about:blank')await page.close().catch(()=>{});
    await browser?.close().catch(()=>{});
  }
}
module.exports={readBrowserSession};
if(require.main===module){
  readBrowserSession().then(cookies=>process.stdout.write(JSON.stringify({ok:true,cookies})))
    .catch(error=>{
      const codes=['browser_credential_missing','browser_unavailable','browser_not_logged_in'];
      process.stdout.write(JSON.stringify({ok:false,code:codes.includes(error.message)?error.message:'browser_connection_failed'}));
      process.exitCode=1;
    });
}
