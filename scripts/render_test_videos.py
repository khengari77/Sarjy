"""Render recorded fixture UI checks to friendly MP4 filenames. No API calls."""
import json
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[1]
output=root/'artifacts/test-videos'
output.mkdir(parents=True,exist_ok=True)
cases=[('demo-session','01-bilingual-chat','Offline UI test: demo identity, Arabic/English toggle, new chat, reload, and missing-service error.'),('voice-picker','02-voice-picker','Offline UI test with a fixture catalog: search, pagination, selection, persistence, and call payload.')]
manifest=[]
for token,name,description in cases:
    sources=[file for file in (output/'ui-raw').rglob('video.webm') if token in str(file.parent)]
    if len(sources)!=1:
        raise SystemExit(f'Expected one recording for {name}, found {len(sources)}')
    target=output/(name+'.mp4')
    subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(sources[0]),'-c:v','libx264','-preset','veryfast','-crf','23','-an','-movflags','+faststart',str(target)],check=True)
    manifest.append({'file':target.name,'source':'offline fixtures','audio':False,'description':description})
for filename,description in [
    ('03-live-arabic-weather.mp4','Live Arabic weather reply using OpenAI, ElevenLabs, LiveKit, and Open-Meteo. Input is typed; this does not verify human microphone input.'),
    ('04-live-arabic-reply.mp4','Live short Arabic greeting with browser-captured assistant speech. Input is typed.'),
    ('05-live-voice-to-voice.mp4','Voice-to-voice check: reused prerecorded Arabic clip through Chromium microphone capture, real Deepgram STT, OpenAI reply, ElevenLabs speech, and browser playback. Includes both captured input and output; this is not a human microphone recording.')
]:
    live=output/filename
    if live.exists():
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type','-of','json',str(live)],capture_output=True,text=True,check=True)
        audio=any(stream.get('codec_type')=='audio' for stream in json.loads(probe.stdout)['streams'])
        manifest.append({'file':live.name,'source':'live providers','audio':audio,'description':description})
for item in manifest:
    target=output/item['file']
    subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-ss','2','-i',str(target),'-frames:v','1',str(target.with_suffix('.png'))],check=True)
(output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
(output/'README.md').write_text('# Sarjy test recordings\n\nGenerated locally; this directory is gitignored.\n\n'+'\n'.join(f"- [{item['file']}]({item['file']}): {item['description']} {'Includes browser-captured assistant audio.' if item['audio'] else ('Silent screen recording; no paid provider inference.' if item['source']=='offline fixtures' else 'Screen-only recording; assistant audio played during the live check but is not included in this file.')}" for item in manifest)+'\n\nLive video includes real wait times, not a latency benchmark. Video/audio alignment uses browser wall-clock timestamps and is approximate. Raw captures are retained in ui-raw/ and live-raw/. Do not present fixture recordings as live-provider results.\n')
print(json.dumps(manifest,ensure_ascii=False,indent=2))
