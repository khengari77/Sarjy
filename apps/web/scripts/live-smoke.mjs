// Opt-in live check: one short reply. Backend spend guards must be enabled.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
const locale = process.argv.find((arg) => arg.startsWith('--locale='))?.slice(9) ?? 'en';
if (locale !== 'en') throw new Error('UNSUPPORTED_SMOKE_LOCALE');
const origin = 'http://127.0.0.1:5173';
const weather = process.argv.includes('--weather');
const recording = process.argv.includes('--record');
const microphone = process.argv.includes('--microphone');
// The microphone guard precedes --check-options so no microphone invocation can
// report success without an English fixture, including --check-options --microphone.
if (microphone) throw new Error('ENGLISH_MICROPHONE_FIXTURE_REQUIRED');
if (process.argv.includes('--check-options')) {
  console.log(JSON.stringify({ locale, callLabel: 'Start call', voiceLabel: 'Choose voice' }));
  process.exit(0);
}
const directory = resolve(dirname(fileURLToPath(import.meta.url)), '../../../artifacts/test-videos');
if (recording) await mkdir(directory, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  args: [
    '--autoplay-policy=no-user-gesture-required',
    ...(microphone
      ? [
          '--use-fake-device-for-media-stream',
          '--use-fake-ui-for-media-stream',
          '--use-file-for-fake-audio-capture=' + resolve(directory, 'microphone-input.wav') + '%noloop',
        ]
      : []),
  ],
});
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  ...(microphone ? { permissions: ['microphone'] } : {}),
  ...(recording ? { recordVideo: { dir: resolve(directory, 'live-raw'), size: { width: 1280, height: 800 } } } : {}),
});
const videoStartedAt = Date.now();
const page = await context.newPage();
let sessionId;
let conversationId;
let failed = false;
await page.addInitScript(
  ({ recording, weather, microphone }) => {
    window.sarjyAudioPlayed = false;
    document.addEventListener(
      'playing',
      (event) => {
        if (event.target instanceof HTMLAudioElement) window.sarjyAudioPlayed = true;
      },
      true,
    );
    if (recording)
      document.addEventListener('DOMContentLoaded', async () => {
        const caption = document.createElement('div');
        caption.textContent =
          'LIVE PROVIDERS · ' +
          (microphone
            ? 'VOICE-TO-VOICE · prerecorded English clip through browser microphone → real STT → reply'
            : weather
              ? 'English weather + real Open-Meteo tool'
              : 'Short English reply') +
          ' · bounded development budget';
        caption.dir = 'ltr';
        caption.style.cssText =
          'position:fixed;inset:0 0 auto 0;z-index:99999;background:#16372eee;color:white;padding:8px 16px;font:13px system-ui;pointer-events:none';
        document.body.appendChild(caption);
        try {
          const audioContext = new AudioContext();
          await audioContext.resume();
          const destination = audioContext.createMediaStreamDestination();
          const silence = audioContext.createConstantSource();
          silence.offset.value = 0;
          silence.connect(destination);
          silence.start();
          const recorder = new MediaRecorder(destination.stream, {
            mimeType: 'audio/webm;codecs=opus',
            audioBitsPerSecond: 64000,
          });
          const state = {
            recorder,
            audioContext,
            destination,
            silence,
            chunks: [],
            startedAt: Date.now(),
            sources: [],
            micAttached: false,
          };
          window.sarjyRecorder = state;
          recorder.ondataavailable = (event) => {
            if (event.data.size) state.chunks.push(event.data);
          };
          recorder.start(1000);
          if (microphone) {
            const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = async (constraints) => {
              if (constraints.audio)
                constraints = {
                  ...constraints,
                  audio: {
                    ...(typeof constraints.audio === 'object' ? constraints.audio : {}),
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false,
                  },
                };
              const stream = await original(constraints);
              if (stream.getAudioTracks().length) {
                const source = audioContext.createMediaStreamSource(stream);
                source.connect(destination);
                state.sources.push(source);
                state.micAttached = true;
              }
              return stream;
            };
          }
          const connected = new WeakSet();
          const connectAudio = () => {
            for (const audio of document.querySelectorAll('audio')) {
              if (
                !connected.has(audio) &&
                audio.srcObject instanceof MediaStream &&
                audio.srcObject.getAudioTracks().length
              ) {
                const source = audioContext.createMediaStreamSource(audio.srcObject);
                source.connect(destination);
                state.sources.push(source);
                connected.add(audio);
              }
            }
          };
          new MutationObserver(connectAudio).observe(document.body, { childList: true, subtree: true });
          document.addEventListener('loadedmetadata', connectAudio, true);
        } catch {
          window.sarjyRecorder = null;
        }
      });
  },
  { recording, weather, microphone },
);
page.on('response', async (response) => {
  if (response.url() === origin + '/api/v1/voice/sessions' && response.request().method() === 'POST') {
    if (response.ok()) sessionId = (await response.json()).id;
    else console.log(JSON.stringify({ call_start_status: response.status() }));
  }
});
try {
  await page.goto(origin);
  await page.getByRole('button', { name: 'Start call', exact: true }).waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('textarea')?.disabled);
  const voicesResponse = await page.request.get(origin + '/api/v1/voices');
  if (!voicesResponse.ok()) throw new Error('VOICE_CATALOG_UNAVAILABLE');
  const voices = (await voicesResponse.json()).voices;
  const chosen = voices.find((voice) =>
    voice.languages?.some((language) => ['en', 'english'].includes(language.toLowerCase())),
  );
  if (!chosen) throw new Error('NO_TEST_VOICE_AVAILABLE');
  // This choice is only for this isolated test browser, not a production default.
  console.log(
    JSON.stringify({
      test_voice: chosen.name,
      english_label: chosen.languages?.some((language) => ['en', 'english'].includes(language.toLowerCase())) || false,
    }),
  );
  await page.getByRole('button', { name: 'Load voices / Search', exact: true }).click();
  await page.waitForFunction((id) => Boolean(document.querySelector(`option[value="${id}"]`)), chosen.id);
  await page.getByLabel('Choose voice', { exact: true }).selectOption(chosen.id);
  const chats = await (await page.request.get(origin + '/api/v1/conversations')).json();
  conversationId = chats[0].id;
  if (microphone) {
    await page.getByRole('button', { name: 'Start call', exact: true }).click();
  } else {
    await page
      .getByRole('textbox', { name: 'Write a message…' })
      .fill(weather ? 'What is the temperature in Tripoli, Libya? Answer briefly.' : 'Say only: Hello.');
    await page.getByRole('button', { name: 'Send ↗', exact: true }).click();
  }
  const deadline = Date.now() + 75000;
  let reply;
  let transcript;
  let micStopped = false;
  while (Date.now() < deadline) {
    const alert = page.getByRole('alert');
    if (await alert.count()) throw new Error((await alert.innerText()).trim());
    const result = await page.request.get(origin + `/api/v1/conversations/${conversationId}/turns`);
    const turns = await result.json();
    transcript = turns.find((turn) => turn.role === 'user');
    if (microphone && transcript && !micStopped) {
      const mute = page.getByRole('button', { name: 'Mute', exact: true });
      if (await mute.count()) {
        await mute.click();
        micStopped = true;
      }
    }
    reply = turns.find((turn) => turn.role === 'assistant' && turn.status === 'completed');
    if (reply) break;
    await page.waitForTimeout(500);
  }
  if (!reply) throw new Error('ASSISTANT_REPLY_TIMEOUT');
  if (microphone) {
    if (!transcript?.text) throw new Error('MICROPHONE_TRANSCRIPT_MISSING');
    console.log(
      JSON.stringify({
        microphone_transcript: transcript.text,
        input_source: 'reused prerecorded English clip; real browser microphone/STT path',
      }),
    );
  }
  console.log(
    JSON.stringify({
      reply: reply.text,
      status: reply.status,
      audio_playback_observed: await page.evaluate(() => window.sarjyAudioPlayed),
    }),
  );
  if (weather) {
    const tools = await (await page.request.get(origin + `/api/v1/conversations/${conversationId}/tool-calls`)).json();
    if (!tools.some((tool) => tool.name === 'get_current_weather' && tool.result.current))
      throw new Error('WEATHER_TOOL_RESULT_MISSING');
    console.log(JSON.stringify({ weather_tool_verified: true }));
  }
  if (!(await page.evaluate(() => window.sarjyAudioPlayed))) throw new Error('AUDIO_PLAYBACK_NOT_OBSERVED');
  if (recording) {
    for (const detail of await page.locator('details').all()) await detail.evaluate((element) => (element.open = true));
    await page.locator('article').last().scrollIntoViewIfNeeded();
    await page.waitForTimeout(2500);
  }
} catch (error) {
  failed = true;
  console.log(JSON.stringify({ live_smoke: 'failed', code: error.message }));
} finally {
  let audio;
  if (recording) {
    audio = await page
      .evaluate(async () => {
        const state = window.sarjyRecorder;
        if (!state || state.recorder.state === 'inactive') return null;
        return await new Promise((resolve) => {
          state.recorder.addEventListener(
            'stop',
            async () => {
              const buffer = await new Blob(state.chunks, { type: 'audio/webm' }).arrayBuffer();
              await state.audioContext.close();
              resolve({
                bytes: Array.from(new Uint8Array(buffer)),
                startedAt: state.startedAt,
                sources: state.sources.length,
                micAttached: state.micAttached,
              });
            },
            { once: true },
          );
          state.recorder.stop();
        });
      })
      .catch(() => null);
  }
  if (sessionId) {
    const response = await page.request.post(origin + `/api/v1/voice/sessions/${sessionId}/end`, {
      headers: { origin },
    });
    console.log(JSON.stringify({ session_cleanup: response.status() }));
  }
  await context.close();
  try {
    if (recording) {
      const name =
        (microphone ? '05-live-voice-to-voice' : weather ? '03-live-english-weather' : '04-live-english-reply') +
        (failed ? '-failed' : '');
      const video = resolve(directory, name + '.webm');
      await page.video().saveAs(video);
      if (audio?.bytes.length && audio.sources) {
        const audioPath = resolve(directory, name + '-audio.webm');
        await writeFile(audioPath, Buffer.from(audio.bytes));
        const offset = Math.max(0, (audio.startedAt - videoStartedAt) / 1000);
        const output = resolve(directory, name + '.mp4');
        const mux = spawnSync(
          'ffmpeg',
          [
            '-y',
            '-hide_banner',
            '-loglevel',
            'error',
            '-i',
            video,
            '-itsoffset',
            String(offset),
            '-i',
            audioPath,
            '-map',
            '0:v',
            '-map',
            '1:a',
            '-c:v',
            'libx264',
            '-preset',
            'veryfast',
            '-crf',
            '23',
            '-c:a',
            'aac',
            '-b:a',
            '96k',
            '-af',
            'apad',
            '-shortest',
            '-movflags',
            '+faststart',
            output,
          ],
          { encoding: 'utf8' },
        );
        if (mux.status !== 0) throw new Error('VIDEO_AUDIO_MUX_FAILED: ' + mux.stderr);
        console.log(JSON.stringify({ recording: output, audio_included: true }));
      } else console.log(JSON.stringify({ recording: video, audio_included: false }));
    }
  } finally {
    await browser.close();
  }
}
if (failed) process.exitCode = 1;
