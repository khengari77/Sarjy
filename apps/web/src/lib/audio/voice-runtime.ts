import { Room, RoomEvent, Track } from 'livekit-client';
import type { SessionConfig } from '../api';
import { waitForAgent, type StartupSignal } from './agentStartup';

type VoiceEventBody =
  | { type: 'status'; source: 'transport' | 'agent'; text: string }
  | { type: 'error'; code: string }
  | { type: 'audio-blocked'; code: string }
  | { type: 'transcript'; text: string; final: boolean; segmentId: string; speaker: 'user' | 'assistant' };

export type VoiceEvent = VoiceEventBody & { epoch: number };

export interface VoiceRuntime {
  readonly epoch: number;
  connect(config: SessionConfig): Promise<void>;
  startMicrophone(): Promise<void>;
  stopMicrophone(): Promise<void>;
  interrupt(): Promise<void>;
  sendText(text: string): Promise<void>;
  disconnect(): Promise<void>;
  cancelConnect(): Promise<void>;
  subscribe(handler: (event: VoiceEvent) => void): () => void;
  resumeAudio?(): Promise<void>;
  microphoneStream?(): MediaStream | null;
  remoteAudioLevel?(): number;
}

export class LiveKitVoiceRuntime implements VoiceRuntime {
  private room: Room | null = null;
  private audio = new Set<HTMLMediaElement>();
  private audioContext: AudioContext | null = null;
  private analysers = new Map<
    HTMLMediaElement,
    { source: MediaElementAudioSourceNode; analyser: AnalyserNode; data: Float32Array }
  >();
  private handlers = new Set<(event: VoiceEvent) => void>();
  private sessionEpoch = 0;
  private sawTranscriptionSegments = false;
  private startup: StartupSignal | null = null;

  get epoch() {
    return this.sessionEpoch;
  }

  subscribe(handler: (event: VoiceEvent) => void) {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  private active(room: Room, epoch: number) {
    return this.room === room && this.sessionEpoch === epoch;
  }

  private emit(event: VoiceEventBody) {
    const tagged = { ...event, epoch: this.sessionEpoch } as VoiceEvent;
    for (const handler of this.handlers) handler(tagged);
  }

  async connect(config: SessionConfig) {
    await this.disconnect();
    this.sessionEpoch += 1;
    const epoch = this.sessionEpoch;
    this.sawTranscriptionSegments = false;
    const startup: StartupSignal = { epoch, error: null, wake: null };
    this.startup = startup;
    const room = new Room();
    this.room = room;

    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (!this.active(room, epoch) || track.kind !== Track.Kind.Audio) return;
      const element = track.attach();
      this.audio.add(element);
      document.body.appendChild(element);
      this.attachAnalyser(element);
    });
    room.on(RoomEvent.TrackUnsubscribed, (track) => {
      if (!this.active(room, epoch)) return;
      for (const element of track.detach()) {
        this.detachAnalyser(element);
        element.remove();
        this.audio.delete(element);
      }
    });
    room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
      if (this.active(room, epoch) && !room.canPlaybackAudio)
        this.emit({ type: 'audio-blocked', code: 'AUDIO_PLAYBACK_BLOCKED' });
    });
    room.on(RoomEvent.Reconnecting, () => {
      if (this.active(room, epoch)) this.emit({ type: 'status', source: 'transport', text: 'reconnecting' });
    });
    room.on(RoomEvent.Reconnected, () => {
      if (this.active(room, epoch)) this.emit({ type: 'status', source: 'transport', text: 'connected' });
    });
    room.on(RoomEvent.Disconnected, () => {
      if (!this.active(room, epoch)) return;
      this.emit({ type: 'status', source: 'transport', text: 'disconnected' });
      if (this.startup === startup) {
        startup.error = new Error('CONNECTION_LOST');
        startup.wake?.();
        startup.wake = null;
      }
    });
    room.on(RoomEvent.ParticipantAttributesChanged, (attributes) => {
      const state = attributes['lk.agent.state'];
      if (this.active(room, epoch) && state) this.emit({ type: 'status', source: 'agent', text: state });
    });
    room.on(RoomEvent.DataReceived, (payload, participant, _kind, topic) => {
      if (!this.active(room, epoch) || topic !== 'sarjy.error' || !participant?.isAgent) return;
      try {
        const event = JSON.parse(new TextDecoder().decode(payload));
        if (['DAILY_BUDGET_EXHAUSTED', 'BUDGET_SERVICE_UNAVAILABLE'].includes(event.code)) {
          this.emit({ type: 'error', code: event.code });
        }
      } catch {
        /* malformed control payload: ignore rather than crash the session */
      }
    });
    room.on(RoomEvent.MediaDevicesError, () => {
      if (this.active(room, epoch)) this.emit({ type: 'error', code: 'MICROPHONE_UNAVAILABLE' });
    });
    // LiveKit exposes real partial/final segments. Prefer them and keep the
    // text-stream reader only as a fallback for publishers that send no
    // segments; mixing both would duplicate each utterance.
    room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
      if (!this.active(room, epoch)) return;
      this.sawTranscriptionSegments = true;
      for (const segment of segments) {
        this.emit({
          type: 'transcript',
          text: segment.text,
          final: segment.final,
          segmentId: segment.id,
          speaker: participant?.isAgent ? 'assistant' : 'user',
        });
      }
    });
    room.registerTextStreamHandler('lk.transcription', async (reader) => {
      try {
        const text = await reader.readAll();
        if (!this.active(room, epoch) || this.sawTranscriptionSegments) return;
        this.emit({ type: 'transcript', text, final: true, segmentId: 'stream', speaker: 'assistant' });
      } catch {
        if (this.active(room, epoch)) this.emit({ type: 'error', code: 'TRANSCRIPT_UNAVAILABLE' });
      }
    });

    try {
      this.emit({ type: 'status', source: 'transport', text: 'connecting' });
      await room.connect(config.url, config.token);
      if (!this.active(room, epoch)) return;
      if (startup.error) throw startup.error;
      await room.startAudio();
      if (!this.active(room, epoch)) return;
      if (startup.error) throw startup.error;
      this.emit({ type: 'status', source: 'transport', text: 'starting' });
      await waitForAgent({
        room,
        epoch,
        startup,
        isActive: (candidate, tag) => this.active(candidate, tag),
        emit: (state) => this.emit({ type: 'status', source: 'agent', text: state }),
      });
    } finally {
      if (this.startup === startup) this.startup = null;
    }
  }

  async resumeAudio() {
    await this.room?.startAudio();
  }

  /** Smoothed agent-output level for the orb; 0 when audio analysis is unavailable. */
  remoteAudioLevel(): number {
    if (!this.analysers.size) return 0;
    let total = 0;
    for (const { analyser, data } of this.analysers.values()) {
      analyser.getFloatTimeDomainData(data as Float32Array<ArrayBuffer>);
      let sum = 0;
      for (const sample of data) sum += sample * sample;
      total += Math.sqrt(sum / data.length);
    }
    return Math.min(1, (total / this.analysers.size) * 4);
  }

  private attachAnalyser(element: HTMLMediaElement) {
    try {
      const AudioCtor =
        window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioCtor) return;
      this.audioContext ??= new AudioCtor();
      const source = this.audioContext.createMediaElementSource(element);
      const analyser = this.audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyser.connect(this.audioContext.destination);
      this.analysers.set(element, { source, analyser, data: new Float32Array(analyser.fftSize) });
    } catch {
      // Analysis is cosmetic: never let it stop remote audio playback.
    }
  }

  private detachAnalyser(element: HTMLMediaElement) {
    const entry = this.analysers.get(element);
    if (!entry) return;
    try {
      entry.source.disconnect();
      entry.analyser.disconnect();
    } catch {
      /* already disconnected */
    }
    this.analysers.delete(element);
  }

  microphoneStream(): MediaStream | null {
    const track = this.room?.localParticipant.getTrackPublication(Track.Source.Microphone)?.track?.mediaStreamTrack;
    return track ? new MediaStream([track]) : null;
  }

  async startMicrophone() {
    await this.room?.localParticipant.setMicrophoneEnabled(true);
  }

  async stopMicrophone() {
    await this.room?.localParticipant.setMicrophoneEnabled(false);
  }

  async sendText(text: string) {
    if (!this.room) throw new Error('CALL_REQUIRED');
    await this.room.localParticipant.sendText(text, { topic: 'lk.chat' });
  }

  async interrupt() {
    const agent = [...(this.room?.remoteParticipants.values() || [])].find((participant) => participant.isAgent);
    if (agent && this.room) {
      await this.room.localParticipant.performRpc({
        destinationIdentity: agent.identity,
        method: 'sarjy.interrupt',
        payload: '',
      });
    }
  }

  /** Abort an in-flight connection attempt and ignore anything it still emits. */
  async cancelConnect() {
    this.sessionEpoch += 1;
    await this.disconnectRoom();
  }

  async disconnect() {
    this.sessionEpoch += 1;
    await this.disconnectRoom();
  }

  private async disconnectRoom() {
    this.startup?.wake?.();
    this.startup = null;
    const room = this.room;
    this.room = null;
    if (room) {
      await room.disconnect();
      room.removeAllListeners();
    }
    for (const element of this.audio) {
      this.detachAnalyser(element);
      element.pause();
      element.srcObject = null;
      element.remove();
    }
    this.audio.clear();
    if (this.audioContext) {
      void this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
  }
}
