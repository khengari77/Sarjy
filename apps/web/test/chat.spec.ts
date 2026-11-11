import {test,expect} from '@playwright/test';
test.beforeEach(async({page},info)=>{
 await page.addInitScript(()=>{if(!localStorage.getItem('sarjy-language'))localStorage.setItem('sarjy-language','ar')});
 if(info.project.name!=='recorded-ui')return;
 await page.addInitScript(({title})=>{
  document.addEventListener('DOMContentLoaded',()=>{
   const caption=document.createElement('div');
   caption.textContent='OFFLINE UI TEST · fixture data · '+title;
   caption.dir='ltr';
   caption.style.cssText='position:fixed;inset:0 0 auto 0;z-index:99999;background:#16372eee;color:white;padding:8px 16px;font:13px system-ui;pointer-events:none';
   document.body.appendChild(caption);
  });
 },{title:info.title});
});
test('demo session, bilingual controls, history and explicit missing-service errors',async({page})=>{
 await page.goto('/');
 await expect(page.getByRole('button',{name:'بدء مكالمة'})).toBeEnabled();
 await expect(page.locator('.shell')).toHaveAttribute('dir','rtl');
 await page.getByRole('button',{name:'English',exact:true}).click();
 await expect(page.locator('.shell')).toHaveAttribute('dir','ltr');
 const before=await page.request.get('/api/v1/conversations');
 expect(before.ok()).toBeTruthy();
 const chats=await before.json();
 await page.getByRole('button',{name:'New conversation +',exact:true}).click();
 await expect.poll(async()=> (await (await page.request.get('/api/v1/conversations')).json()).length).toBe(chats.length+1);
 await page.reload();
 await expect(page.locator('.shell')).toHaveAttribute('dir','ltr');
 await page.getByRole('button',{name:'Start call',exact:true}).click();
 await expect(page.getByRole('alert')).toHaveText('PIPELINE_NOT_CONFIGURED');
});
test('proxy rejects cross-origin mutation',async({request})=>{
 const result=await request.post('/api/v1/demo-session',{headers:{origin:'https://untrusted.example'}});
 expect(result.status()).toBe(403);
});

test('voice picker search, pagination, saved preference and call payload',async({page})=>{
 await page.route('**/api/v1/voices?*',async route=>{
  const query=new URL(route.request().url()).searchParams;
  expect(query.get('search')).toBe('British');
  const more=query.get('next_page_token')==='second';
  await route.fulfill({json:{voices:more?[{id:'lady-voice',name:'British young lady',languages:['en']}]:[{id:'first-voice',name:'British narrator',languages:['en']}],next_page_token:more?null:'second'}});
 });
 let sentVoice:string|undefined;
 await page.route('**/api/v1/voice/sessions',async route=>{
  sentVoice=route.request().postDataJSON().voice_id;
  await route.fulfill({status:503,json:{detail:{code:'PIPELINE_NOT_CONFIGURED'}}});
 });
 await page.goto('/');
 await expect(page.getByRole('button',{name:'بدء مكالمة'})).toBeEnabled();
 await page.getByRole('button',{name:'English',exact:true}).click();
 await page.getByRole('textbox',{name:'Search voices'}).fill('British');
 await page.getByRole('button',{name:'Load voices / Search',exact:true}).click();
 await page.getByRole('button',{name:'Load more',exact:true}).click();
 await page.getByLabel('Choose voice').selectOption('lady-voice');
 await page.reload();
 await expect(page.getByLabel('Choose voice')).toHaveValue('lady-voice');
 await page.getByRole('button',{name:'Start call',exact:true}).click();
 await expect(page.getByRole('alert')).toHaveText('PIPELINE_NOT_CONFIGURED');
 expect(sentVoice).toBe('lady-voice');
});
