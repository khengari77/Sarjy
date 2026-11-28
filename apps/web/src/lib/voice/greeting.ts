/**
 * Browser-played greeting audio. The worker is silent for these sessions, so
 * the clip is the only thing masking its cold start.
 */

export interface VoiceGreeting {
  text: string;
  kind: 'full' | 'short';
}

/** Internal greeting record; the attempt id survives Svelte's state proxying. */
export interface ActiveGreeting extends VoiceGreeting {
  attempt: number;
}

export interface GreetingPlayer {
  /** Unlock audio inside the click's call stack, before any await. */
  prepare(): void;
  /** Resolve on the ended event, or 'failed' on error, rejection, 404, stop or a stalled clip. Never rejects. */
  play(url: string): Promise<'ended' | 'failed'>;
  stop(): void;
  readonly playing: boolean;
}

/** Well over twice the longest clip (~20 s), so only a genuinely stalled clip hits it. */
export const GREETING_MAX_PLAY_MS = 45_000;

export interface GreetingPlayerOptions {
  maxPlayMs?: number;
}

/** Stop the clip and drop its caption. */
export function clearGreeting(state: { greeting: ActiveGreeting | null }, player: GreetingPlayer): void {
  player.stop();
  state.greeting = null;
}

/** One silent frame, played muted to unlock the element for later playback. */
const SILENT_AUDIO = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';

/** The server's configured default when the owner has saved no voice. */
const DEFAULT_GREETING_VOICE = 'aura-2-thalia-en';

interface ActivePlayback {
  audio: HTMLAudioElement;
  resolve(outcome: 'ended' | 'failed'): void;
  onEnded(): void;
  onError(): void;
  timeout: ReturnType<typeof setTimeout> | null;
}

export function createBrowserGreetingPlayer(options: GreetingPlayerOptions = {}): GreetingPlayer {
  const maxPlayMs = options.maxPlayMs ?? GREETING_MAX_PLAY_MS;
  let element: HTMLAudioElement | null = null;
  let active: ActivePlayback | null = null;
  let playToken = 0;

  function ensureElement(): HTMLAudioElement | null {
    if (element) return element;
    try {
      if (typeof Audio === 'undefined') return null;
      element = new Audio();
      element.preload = 'auto';
    } catch {
      element = null;
    }
    return element;
  }

  function finish(outcome: 'ended' | 'failed') {
    const current = active;
    if (!current) return;
    active = null;
    if (current.timeout !== null) clearTimeout(current.timeout);
    current.audio.removeEventListener('ended', current.onEnded);
    current.audio.removeEventListener('error', current.onError);
    current.resolve(outcome);
  }

  return {
    prepare() {
      const audio = ensureElement();
      if (!audio || active) return;
      try {
        audio.muted = true;
        audio.src = SILENT_AUDIO;
        audio.load();
        const started = audio.play();
        if (started && typeof started.catch === 'function') started.catch(() => {});
      } catch {
        /* unlock is best effort: play() still reports a blocked autoplay */
      }
    },

    play(url) {
      const audio = ensureElement();
      if (!audio) return Promise.resolve('failed');
      finish('failed');
      playToken += 1;
      // Stop the previous clip before swapping sources so its queued events
      // cannot resolve the new playback.
      try {
        audio.pause();
      } catch {
        /* pause is best effort */
      }
      return new Promise<'ended' | 'failed'>((resolve) => {
        const token = playToken;
        const onEnded = () => {
          if (token === playToken) finish('ended');
        };
        const onError = () => {
          if (token === playToken) finish('failed');
        };
        const timeout =
          maxPlayMs > 0
            ? setTimeout(() => {
                if (token === playToken) finish('failed');
              }, maxPlayMs)
            : null;
        active = { audio, resolve, onEnded, onError, timeout };
        try {
          audio.muted = false;
          audio.src = url;
        } catch {
          if (token === playToken) finish('failed');
          return;
        }
        audio.addEventListener('ended', onEnded);
        audio.addEventListener('error', onError);
        try {
          const started = audio.play();
          if (started && typeof started.catch === 'function') {
            started.catch(() => {
              if (token === playToken) finish('failed');
            });
          }
        } catch {
          if (token === playToken) finish('failed');
        }
      });
    },

    stop() {
      const audio = element;
      finish('failed');
      if (!audio) return;
      try {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
      } catch {
        /* stopping is best effort */
      }
    },

    get playing() {
      return active !== null;
    },
  };
}

/** Warm the two clips for the voice this browser is most likely to hear. */
export function prefetchGreetings(voiceId: string | null | undefined): void {
  if (typeof fetch === 'undefined') return;
  const voice = voiceId || DEFAULT_GREETING_VOICE;
  for (const kind of ['full', 'short'] as const) {
    try {
      void fetch(`/greetings/${encodeURIComponent(voice)}/${kind}.mp3`, {
        cache: 'force-cache',
        priority: 'low',
      }).catch(() => {});
    } catch {
      /* prefetch is best effort */
    }
  }
}
