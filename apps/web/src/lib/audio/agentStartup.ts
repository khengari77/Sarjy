import type { Room } from 'livekit-client';

export interface StartupSignal {
  epoch: number;
  error: Error | null;
  wake: (() => void) | null;
}

export interface AgentStartupOptions {
  room: Room;
  epoch: number;
  startup: StartupSignal;
  isActive(room: Room, epoch: number): boolean;
  emit(state: string): void;
}

export async function waitForAgent(options: AgentStartupOptions): Promise<void> {
  const { room, epoch, startup } = options;
  const deadline = Date.now() + 45000;
  const aborted = new Promise<void>((resolve) => {
    startup.wake = resolve;
  });
  while (options.isActive(room, epoch) && Date.now() < deadline) {
    if (startup.error) throw startup.error;
    const agent = [...room.remoteParticipants.values()].find((participant) => participant.isAgent);
    const state = agent?.attributes['lk.agent.state'];
    if (state && ['listening', 'thinking', 'speaking'].includes(state)) {
      options.emit(state);
      return;
    }
    await Promise.race([new Promise((resolve) => setTimeout(resolve, 100)), aborted]);
  }
  if (!options.isActive(room, epoch)) return;
  if (startup.error) throw startup.error;
  throw new Error('AGENT_START_TIMEOUT');
}
