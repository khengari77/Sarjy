import { request } from '../api';
import type { SessionConfig } from '../api';
import type { VoiceEvent, VoiceRuntime } from '../audio/voice-runtime';

export interface PageLifecycleOptions {
  runtime: VoiceRuntime;
  onEvent(event: VoiceEvent): void;
  bootstrap(): Promise<void>;
  getLive(): SessionConfig | null;
  endExpired(): void;
  refresh(): Promise<void>;
  /** Stop a playing greeting when the page is hidden or the app unmounts. */
  stopGreeting?(): void;
}

export function bindVoicePage(options: PageLifecycleOptions): () => void {
  const unsubscribe = options.runtime.subscribe((event) => {
    if (event.epoch !== options.runtime.epoch) return;
    options.onEvent(event);
  });
  void options.bootstrap();

  const endOnPageHide = () => {
    options.stopGreeting?.();
    const live = options.getLive();
    if (!live) return;
    try {
      void fetch('/api/v1/voice/sessions/' + live.id + '/end', {
        method: 'POST',
        keepalive: true,
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
    } catch {
      /* best effort: worker departure remains the fallback */
    }
  };
  window.addEventListener('pagehide', endOnPageHide);

  const poll = setInterval(() => {
    const live = options.getLive();
    if (live && Date.parse(live.expires_at) <= Date.now()) options.endExpired();
    else if (live) void options.refresh().catch(() => {});
  }, 2500);

  return () => {
    options.stopGreeting?.();
    window.removeEventListener('pagehide', endOnPageHide);
    clearInterval(poll);
    unsubscribe();
    void options.runtime.disconnect();
    const live = options.getLive();
    if (live) void request('voice/sessions/' + live.id + '/end', {}).catch(() => {});
  };
}
