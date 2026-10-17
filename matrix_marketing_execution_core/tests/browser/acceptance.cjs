const {chromium,request}=require('playwright');
(async()=>{
const base=process.env.MATRIX_BROWSER_URL||'http://127.0.0.1:8003';
if(!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('This synthetic fixture is restricted to localhost');
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1365,height:1000}});const errors=[];
page.on('pageerror',e=>errors.push(e.message));
try{
const api=await request.newContext({baseURL:base});
const created=await api.post('/auth/users',{headers:{'X-Admin-Key':'browser-synthetic-bootstrap-key'},data:{username:'browser-owner',password:'synthetic-browser-password',role:'admin',client_ids:[]}});
if(!created.ok())throw Error('Bootstrap failed: '+created.status());
await page.goto(base+'/console');
await page.locator('#username').fill('browser-owner');await page.locator('#password').fill('synthetic-browser-password');
await page.getByRole('button',{name:'Sign in',exact:true}).click();
await page.locator('#workspace').waitFor({state:'visible'});
await page.getByText('Administrator: create a client and select its project profile',{exact:true}).click();
await page.locator('#newClientId').fill('acceptance-client');await page.locator('#newClientName').fill('Synthetic Lakhotia');
await page.getByRole('button',{name:'Create client and assign profile',exact:true}).click();
await page.waitForFunction(()=>document.querySelector('#client').value==='acceptance-client');
await page.getByRole('button',{name:'1 · Import',exact:true}).click();
await page.locator('#csv').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from('source_id,external_id,name,email,email_allowed\nbrowser,one,Synthetic Person,synthetic@example.test,true\n')});
await page.getByRole('button',{name:'Preview file',exact:true}).click();
await page.waitForFunction(()=>document.querySelector('#preview').textContent.includes('1 records'));
await page.getByRole('button',{name:'Import previewed records',exact:true}).click();
await page.waitForFunction(()=>document.querySelector('#leads').textContent.includes('Synthetic Person'));
await page.getByRole('button',{name:'2 · Qualify',exact:true}).click();
await page.getByRole('button',{name:'Load leads and scores',exact:true}).click();
await page.waitForFunction(()=>document.querySelector('#qualLead').options.length===1);
await page.locator('#use').selectOption('manufacturing');await page.locator('#location').fill('Howrah');await page.locator('#land').fill('1');await page.locator('#decision').selectOption('true');await page.locator('#evidence').fill('Synthetic explicit enquiry for browser acceptance');
await page.getByRole('button',{name:'Save requirement and calculate score',exact:true}).click();
await page.waitForFunction(()=>document.querySelector('#scores').textContent.includes('100'));
await page.locator('#reviewDecision').selectOption('qualified');await page.locator('#reviewReason').fill('Synthetic requirement checked');
await page.getByRole('button',{name:'Save review decision',exact:true}).click();
await page.waitForFunction(()=>document.querySelector('#scores').textContent.includes('qualified'));
await page.getByRole('button',{name:'3 · Build',exact:true}).click();
await page.locator('#campaignName').fill('Browser acceptance rehearsal');await page.locator('#message').fill('Synthetic message — no live delivery');
await page.getByRole('button',{name:'Save draft',exact:true}).click();
await page.locator('#approve.active').waitFor();
for(const [button,status] of [['Check campaign','validated'],['Approve simulation','approved'],['Queue safe rehearsal','simulated']]){
await page.getByRole('button',{name:button,exact:true}).click();
await page.waitForFunction(s=>document.querySelector('#operation').textContent.includes('"'+s+'"'),status);
}
await page.getByRole('button',{name:'5 · Monitor',exact:true}).click();
await page.getByRole('button',{name:'Refresh progress',exact:true}).click();
await page.waitForFunction(()=>document.querySelector('#jobs').textContent.includes('done'));
await page.getByRole('button',{name:'6 · Results',exact:true}).click();
await page.getByRole('button',{name:'Load report',exact:true}).click();
await page.waitForFunction(()=>document.querySelector('#reportData').textContent.includes('"mock_jobs": 1'));
if(errors.length)throw Error(errors.join('\n'));
await page.screenshot({path:'/tmp/pratha-desktop-acceptance.png',fullPage:true});
await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/pratha-mobile-acceptance.png',fullPage:true});
const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
if(overflow)throw Error('Mobile page has horizontal overflow');
console.log(JSON.stringify({browser_flow:'passed',mock_jobs:1,real_deliveries:0,page_errors:errors.length,mobile_overflow:overflow}));
}finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exit(1)});
