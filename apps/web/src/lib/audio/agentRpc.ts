import { ConnectionState, type Participant, type Room } from 'livekit-client';

/**
 * User-facing RPCs that are not part of the conversational pipeline.
 *
 * The agent speaks exact text through `sarjy.say` (no LLM turn) and receives
 * control flags on the `sarjy.control` data channel. Both are best-effort
 * transports: the caller decides whether a missing room is an error.
 */
export function agentParticipant(room: Room | null): Participant | undefined {
  return [...(room?.remoteParticipants.values() || [])].find((participant) => participant.isAgent);
}

/** Speak exact text through the agent's say channel; never invokes the LLM. */
export async function speakExact(room: Room | null, text: string): Promise<void> {
  const agent = agentParticipant(room);
  if (!room || !agent) throw new Error('CALL_REQUIRED');
  await room.localParticipant.performRpc({
    destinationIdentity: agent.identity,
    method: 'sarjy.say',
    payload: JSON.stringify({ text }),
  });
}

/** Ask the agent to stop speaking. A room without an agent is already quiet. */
export async function interruptAgent(room: Room | null): Promise<void> {
  const agent = agentParticipant(room);
  if (!agent || !room) return;
  await room.localParticipant.performRpc({
    destinationIdentity: agent.identity,
    method: 'sarjy.interrupt',
    payload: '',
  });
}

/** Send a chat message the agent answers conversationally. */
export async function sendChatText(room: Room | null, text: string): Promise<void> {
  if (!room) throw new Error('CALL_REQUIRED');
  await room.localParticipant.sendText(text, { topic: 'lk.chat' });
}

/**
 * Publish a validated control flag to the worker without creating a turn.
 * Without a live room the flag is dropped: it describes a transient UI state.
 */
export async function publishControlFlag(room: Room | null, event: Record<string, unknown>): Promise<void> {
  // Publishing while the room is still connecting left livekit-client unable to send any data for the rest
  // of the call ("PC manager is closed"), so control flags wait for a connected room. They are re-sent on
  // every later status event, so nothing is lost.
  if (!room || room.state !== ConnectionState.Connected) return;
  await room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify(event)), {
    reliable: true,
    topic: 'sarjy.control',
  });
}
