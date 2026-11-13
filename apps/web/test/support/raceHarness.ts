import { expect, type Page } from '@playwright/test';

export const CHATS = [
  { id: 'c1', title: 'Chat A', revision: 0 },
  { id: 'c2', title: 'Chat B', revision: 0 },
];

export interface RaceOptions {
  autoConnect?: boolean;
  startMicFail?: boolean;
  deferStartMic?: boolean;
  deferDisconnect?: boolean;
}

declare global {
  interface Window {
    __race?: {
      autoConnect: boolean;
      startMicFail: boolean;
      audioLevel: number;
      connects: string[];
      disconnects: number;
      cancels: number;
      sent: string[];
      resolveConnect(id: string): void;
      hasPending(id: string): boolean;
      resolveStartMic(): void;
      hasPendingStartMic(): boolean;
      resolveDisconnect(): void;
      hasPendingDisconnect(): boolean;
      setStartMicFail(value: boolean): void;
      stats(): { connects: string[]; disconnects: number; cancels: number; sent: string[] };
    };
    __permission?: { resolve(): void; reject(): void };
    __SARJY_VOICE_RUNTIME_FACTORY__?: () => unknown;
  }
}

export async function mockConversations(page: Page) {
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: CHATS })
      : route.fulfill({ json: { id: 'c-new', title: 'New conversation', revision: 0 } }),
  );
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
}

export async function mockSessions(page: Page) {
  const posts: { id: string; body: Record<string, unknown> }[] = [];
  const ends: string[] = [];
  await page.route('**/api/v1/voice/sessions', async (route) => {
    const id = `sess-${posts.length + 1}`;
    posts.push({ id, body: route.request().postDataJSON() as Record<string, unknown> });
    await route.fulfill({
      json: { id, url: 'ws://fake.invalid', token: 'fake', expires_at: new Date(Date.now() + 600_000).toISOString() },
    });
  });
  await page.route('**/api/v1/voice/sessions/*/end', async (route) => {
    ends.push(route.request().url());
    await route.fulfill({ json: {} });
  });
  return { posts, ends };
}

export async function installRaceRuntime(page: Page, options: RaceOptions = {}) {
  await page.addInitScript(
    ({ autoConnect, startMicFail, deferStartMic, deferDisconnect }) => {
      const race = {
        autoConnect,
        startMicFail,
        deferStartMic,
        deferDisconnect,
        audioLevel: 0,
        connects: [] as string[],
        disconnects: 0,
        cancels: 0,
        sent: [] as string[],
        pending: new Map<string, () => void>(),
        pendingStartMic: null as (() => void) | null,
        pendingDisconnect: [] as (() => void)[],
        resolveConnect(id: string) {
          this.pending.get(id)?.();
        },
        hasPending(id: string) {
          return this.pending.has(id);
        },
        resolveStartMic() {
          this.pendingStartMic?.();
          this.pendingStartMic = null;
        },
        hasPendingStartMic() {
          return this.pendingStartMic !== null;
        },
        resolveDisconnect() {
          this.pendingDisconnect.shift()?.();
        },
        hasPendingDisconnect() {
          return this.pendingDisconnect.length > 0;
        },
        setStartMicFail(value: boolean) {
          this.startMicFail = value;
        },
        stats() {
          return {
            connects: [...this.connects],
            disconnects: this.disconnects,
            cancels: this.cancels,
            sent: [...this.sent],
          };
        },
      };

      class FakeRuntime {
        __epoch = 0;
        handlers = new Set<(event: unknown) => void>();
        get epoch() {
          return this.__epoch;
        }
        subscribe(handler: (event: unknown) => void) {
          this.handlers.add(handler);
          return () => this.handlers.delete(handler);
        }
        emit(event: Record<string, unknown>) {
          for (const handler of [...this.handlers]) handler({ ...event, epoch: this.__epoch });
        }
        connect(config: { id: string }) {
          const epoch = ++this.__epoch;
          race.connects.push(config.id);
          return new Promise<void>((resolve) => {
            const finish = () => {
              if (this.__epoch === epoch) {
                this.emit({ type: 'status', source: 'transport', text: 'connected' });
                this.emit({ type: 'status', source: 'agent', text: 'listening' });
              }
              resolve();
            };
            race.pending.set(config.id, finish);
            if (race.autoConnect) queueMicrotask(finish);
          });
        }
        async disconnect() {
          race.disconnects += 1;
          this.__epoch += 1;
          if (race.deferDisconnect) {
            await new Promise<void>((resolve) => race.pendingDisconnect.push(resolve));
          }
        }
        async cancelConnect() {
          race.cancels += 1;
          this.__epoch += 1;
        }
        async startMicrophone() {
          if (race.startMicFail) throw Object.assign(new Error('denied'), { name: 'NotAllowedError' });
          if (race.deferStartMic) {
            await new Promise<void>((resolve) => {
              race.pendingStartMic = resolve;
            });
          }
        }
        async stopMicrophone() {}
        async sendText(text: string) {
          race.sent.push(text);
        }
        async resumeAudio() {}
        async interrupt() {}
        microphoneStream() {
          return null;
        }
        remoteAudioLevel() {
          return race.audioLevel;
        }
      }

      window.__race = race;
      window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => new FakeRuntime();
    },
    {
      autoConnect: options.autoConnect ?? false,
      startMicFail: options.startMicFail ?? false,
      deferStartMic: options.deferStartMic ?? false,
      deferDisconnect: options.deferDisconnect ?? false,
    },
  );
}

export async function allowMicrophone(page: Page) {
  await page.addInitScript(() => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => stream },
    });
  });
}

export async function deferMicrophone(page: Page) {
  await page.addInitScript(() => {
    const permission = { resolve: () => {}, reject: () => {} };
    const pending = new Promise<MediaStream>((resolve, reject) => {
      permission.resolve = () => resolve({ getTracks: () => [] } as unknown as MediaStream);
      permission.reject = () => reject(Object.assign(new Error('denied'), { name: 'NotAllowedError' }));
    });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: () => pending },
    });
    window.__permission = permission;
  });
}

export async function ready(page: Page) {
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
}
