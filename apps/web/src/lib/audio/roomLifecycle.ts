import { Track, type Room, type RoomEventCallbacks } from 'livekit-client';

function attempt(work: () => void) {
  try {
    work();
  } catch {
    console.debug('[Sarjy] Local room cleanup failed');
  }
}

export function bindRoomEvents(room: Room) {
  const releases: (() => void)[] = [];
  return {
    on<E extends keyof RoomEventCallbacks>(event: E, handler: RoomEventCallbacks[E]) {
      room.on(event, handler);
      releases.push(() => {
        room.off(event, handler);
      });
    },
    dispose() {
      for (const release of releases.splice(0)) attempt(release);
      attempt(() => room.unregisterTextStreamHandler('lk.transcription'));
    },
  };
}

export function disconnectRoomInBackground(room: Room | null) {
  if (!room) return;
  attempt(() => room.localParticipant.getTrackPublication(Track.Source.Microphone)?.track?.stop());
  try {
    void room.disconnect().catch(() => console.debug('[Sarjy] Background room disconnect failed'));
  } catch {
    console.debug('[Sarjy] Background room disconnect failed');
  }
}
