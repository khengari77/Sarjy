<script lang="ts">
 import {onMount} from 'svelte';
 import {request, type Conversation, type Turn, type SessionConfig, type StartVoice, type VoiceOption, type VoiceCatalog} from '../lib/api';
 import {LiveKitVoiceRuntime} from '../lib/audio/voice-runtime';
 let arabic = $state(false);
 let voiceId = $state('');
 let voices = $state<VoiceOption[]>([]);
 let voiceSearch = $state('');
 let loadedVoiceSearch = '';
 let nextVoicePage = $state<string|null>(null);
 let voiceLoading = $state(false);
 let voiceError = $state('');
 let activeVoiceName = $state('');
 async function loadVoices(more=false){
  if(voiceLoading)return;
  voiceLoading=true;voiceError='';
  try{
   const search=more?loadedVoiceSearch:voiceSearch;
   const query=new URLSearchParams({search});
   if(more && nextVoicePage)query.set('next_page_token',nextVoicePage);
   const result=await request<VoiceCatalog>('voices?'+query);
   voices=more?[...new Map([...voices,...result.voices].map(voice=>[voice.id,voice])).values()]:result.voices;
   nextVoicePage=result.next_page_token ?? null;
   loadedVoiceSearch=search;
  }catch(e){voiceError=e instanceof Error?e.message:'VOICE_CATALOG_UNAVAILABLE'}finally{voiceLoading=false}
 }
 function rememberVoice(){localStorage.setItem('sarjy-voice-id',voiceId)}
 let conversations = $state<Conversation[]>([]);
 let toolCalls = $state<{id:string;name:string;result:unknown}[]>([]);
 let current = $state<string|null>(null);
 let turns = $state<Turn[]>([]);
 let draft = $state('');
 let status = $state('idle');
 let error = $state('');
 let busy = $state(false);
 let muted = $state(true);
 let audioBlocked = $state(false);
 let live = $state<SessionConfig|null>(null);
 let runtime:LiveKitVoiceRuntime;
 const words = $derived(arabic ? {new:'محادثة جديدة', title:'تحدث. تذكّر. تحكّم بالسياق.', intro:'مساعدك الصوتي، وسياقه بين يديك.', placeholder:'اكتب رسالتك…', send:'إرسال',call:'بدء مكالمة',end:'إنهاء',mute:'كتم الصوت',unmute:'تشغيل الميكروفون'} : {new:'New conversation',title:'Speak. Remember. Shape the context.',intro:'Your voice assistant, with context you control.',placeholder:'Write a message…',send:'Send',call:'Start call',end:'End call',mute:'Mute',unmute:'Enable microphone'});
 async function refresh(){const id=current;if(id){const [history,tools]=await Promise.all([request<Turn[]>('conversations/'+id+'/turns'),request<typeof toolCalls>('conversations/'+id+'/tool-calls')]);if(current===id){turns=history;toolCalls=tools}}}
 async function run(work:()=>Promise<void>){busy=true;error='';try{await work()}catch(e){error=e instanceof Error?e.message:'UNAVAILABLE'}finally{busy=false}}
 async function end(){const session=live;live=null;muted=true;await runtime?.disconnect();if(session)await request('voice/sessions/'+session.id+'/end',{});status='idle';await refresh()}
 async function select(id:string){if(live)await end();current=id;await refresh()}
 async function create(){await run(async()=>{const chat=await request<Conversation>('conversations',{});conversations=[chat,...conversations];await select(chat.id)})}
 async function connect(microphone:boolean){if(!current)throw new Error('CONVERSATION_REQUIRED');if(!live){const requestedVoiceId=voiceId;const requestedVoiceName=voices.find(voice=>voice.id===requestedVoiceId)?.name || requestedVoiceId;const config=await request<SessionConfig>('voice/sessions',{conversation_id:current,language:arabic?'ar':'en',voice_id:requestedVoiceId || null,response_preference:'automatic'} satisfies StartVoice);try{await runtime.connect(config);live=config;activeVoiceName=requestedVoiceName}catch(e){await runtime.disconnect();await request('voice/sessions/'+config.id+'/end',{});throw e}}if(microphone){await runtime.startMicrophone();muted=false}}
 async function send(){if(!draft.trim())return;await run(async()=>{await connect(false);await runtime.sendText(draft.trim());draft=''})}
 onMount(()=>{
  arabic=localStorage.getItem('sarjy-language')==='ar';
  voiceId=localStorage.getItem('sarjy-voice-id') || '';
  runtime=new LiveKitVoiceRuntime();
  const unsubscribe=runtime.subscribe(event=>{if(event.type==='status'){status=event.text;if(event.text==='disconnected' && live)void run(end);}if(event.type==='error')error=event.text;if(event.type==='audio-blocked')audioBlocked=true;if(event.type==='transcript')void refresh().catch(()=>{})});
  void run(async()=>{await request('demo-session',{});conversations=await request<Conversation[]>('conversations');if(conversations.length)await select(conversations[0].id);else{const chat=await request<Conversation>('conversations',{});conversations=[chat];current=chat.id}});
  const poll=setInterval(()=>{if(live && Date.parse(live.expires_at)<=Date.now())void run(end);else if(live)void refresh().catch(()=>{})},2500);
  return ()=>{clearInterval(poll);unsubscribe();void runtime.disconnect();if(live)void request('voice/sessions/'+live.id+'/end',{}).catch(()=>{})};
 });
 function toggleLanguage(){arabic=!arabic;localStorage.setItem('sarjy-language',arabic?'ar':'en')}
</script>
<svelte:head><title>Sarjy — Voice Context Studio</title><meta name="description" content="A bilingual voice assistant with transparent context."/></svelte:head>
<div class="shell" dir={arabic ? 'rtl':'ltr'}>
 <aside><strong>سَرْجي <span>SARJY</span></strong><button disabled={busy} onclick={create}>{words.new} +</button>{#each conversations as chat (chat.id)}<button class:chosen={current===chat.id} disabled={busy} onclick={()=>run(()=>select(chat.id))}>{chat.title}</button>{/each}<p>Voice Context Studio</p></aside>
 <main><header><span>● {status}</span><button onclick={toggleLanguage}>{arabic ? 'English':'العربية'}</button></header>{#if error}<p role="alert">{error}</p>{/if}{#if audioBlocked}<button onclick={()=>run(async()=>{await runtime.resumeAudio();audioBlocked=false})}>Enable audio / تشغيل الصوت</button>{/if}<section>{#if turns.length}{#each turns as turn (turn.id)}<article dir="auto"><small>{turn.role} · {turn.status}</small><p>{turn.text}</p></article>{/each}{:else}<div class="orb">س</div><h1>{words.title}</h1><p>{words.intro}</p>{/if}{#each toolCalls as tool (tool.id)}<details><summary>{tool.name} · Open-Meteo</summary><pre dir="ltr">{JSON.stringify(tool.result,null,2)}</pre></details>{/each}</section><fieldset class="voice-settings">
 <legend>{arabic?'صوت المساعد':'Assistant voice'}</legend>
 <form onsubmit={(event)=>{event.preventDefault();void loadVoices()}}>
  <input aria-label={arabic?'البحث عن صوت':'Search voices'} bind:value={voiceSearch} maxlength="120" placeholder={arabic?'ابحث باسم الصوت أو اللغة':'Search by voice name or language'} />
  <button type="submit" disabled={voiceLoading}>{arabic?'تحميل الأصوات / بحث':'Load voices / Search'}</button>
 </form>
 <label for="assistant-voice">{arabic?'اختر الصوت':'Choose voice'}</label>
 <select id="assistant-voice" bind:value={voiceId} onchange={rememberVoice}>
  <option value="">{arabic?'اختر صوتًا قبل بدء المكالمة':'Select a voice before calling'}</option>
  {#if voiceId && !voices.some(voice=>voice.id===voiceId)}<option value={voiceId}>{arabic?'الصوت المحفوظ (أعد تحميل القائمة)':'Saved voice (reload list)'}</option>{/if}
  {#each voices as voice (voice.id)}<option value={voice.id}>{voice.name}{voice.languages?.length?' · '+voice.languages.join(', '):''}</option>{/each}
 </select>
 {#if nextVoicePage}<button disabled={voiceLoading} onclick={()=>loadVoices(true)}>{arabic?'المزيد':'Load more'}</button>{/if}
 {#if voiceError}<p class="voice-error">{arabic?'تعذّر تحميل الأصوات':'Could not load voices'}: {voiceError}</p>{/if}
 {#if voices.length===0 && !voiceLoading && !voiceError}<small>{arabic?'حمّل الأصوات المتاحة لاختيار صوت مناسب للعربية.':'Load available voices to choose an English voice.'}</small>{/if}
 <small>{arabic?'التغييرات تسري على المكالمة التالية. تسميات اللغة لا تضمن جودة النطق.':'Changes apply to the next call. Language labels do not guarantee pronunciation quality.'}</small>
 {#if live}<small>{arabic?'صوت المكالمة الحالية':'Current call voice'}: {activeVoiceName}</small>{/if}
</fieldset><nav>{#if live}<button disabled={busy} onclick={()=>run(end)}>{words.end}</button><button onclick={()=>run(async()=>{if(muted)await runtime.startMicrophone();else await runtime.stopMicrophone();muted=!muted})}>{muted?words.unmute:words.mute}</button><button onclick={()=>run(()=>runtime.interrupt())}>Stop / إيقاف</button>{:else}<button disabled={busy || !current} onclick={()=>run(()=>connect(true))}>{words.call}</button>{/if}</nav><footer><textarea bind:value={draft} aria-label={words.placeholder} placeholder={words.placeholder} disabled={busy || !current}></textarea><button disabled={busy || !draft.trim()} onclick={send}>{words.send} ↗</button></footer><small><a href="https://open-meteo.com/">Weather data: Open-Meteo</a></small></main>
</div>
<style>
 :global(body){margin:0;font-family:system-ui,sans-serif;background:#f8f7f3;color:#263d38}*{box-sizing:border-box}.shell{display:flex;min-height:100vh}aside{width:260px;background:#eeeee7;padding:32px;display:flex;flex-direction:column;gap:30px}strong{font-size:30px}strong span{display:block;font-size:11px;letter-spacing:4px}button{border:1px solid #cdd4ca;background:transparent;padding:12px 18px;border-radius:10px;color:inherit}button:enabled{cursor:pointer}button:disabled{opacity:.6}aside p{font-size:12px;margin-top:auto}main{flex:1;display:flex;flex-direction:column;padding:24px 5vw}header{display:flex;align-items:center;justify-content:space-between;font-size:12px}section{margin:auto;width:100%;max-width:850px;text-align:center;padding:64px 0}article{text-align:start;background:white;border:1px solid #ddd;padding:16px;border-radius:14px;margin-bottom:16px;overflow-wrap:anywhere}article p{white-space:pre-wrap;color:#263d38}.voice-settings{border:1px solid #cdd4ca;border-radius:14px;padding:16px;margin-bottom:16px;display:grid;gap:10px}.voice-settings form{display:flex;gap:8px;flex-wrap:wrap}.voice-settings input,.voice-settings select{font:inherit;padding:10px;border:1px solid #cdd4ca;border-radius:8px;background:white;max-width:100%}.voice-settings input{flex:1;min-width:140px}.voice-settings small{display:block;color:#52665d}.voice-error{color:#933}nav{display:flex;gap:10px;margin-bottom:12px}a{color:inherit}.chosen{background:#d9e9dd}h1{font-size:clamp(26px,4vw,48px);max-width:650px;font-weight:500}.orb{margin:auto;border-radius:50%;background:#d9e9dd;width:100px;height:100px;display:grid;place-items:center;font-size:48px}p{color:#728078}footer{display:flex;gap:12px;padding-bottom:24px}textarea{flex:1;resize:none;padding:18px;border:1px solid #d4d9d0;border-radius:14px;background:white;font:inherit}@media(max-width:650px){aside{display:none}main{padding:20px}header span{max-width:180px}}
</style>
