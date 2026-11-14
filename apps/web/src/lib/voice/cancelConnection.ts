import type { SessionConfig } from '../api';
import type { ConnectionDeps, VoiceState } from './connection';
import { applyFailedRelease } from './releaseOutcome';

export interface CancelContext {
  state: VoiceState;
  deps: ConnectionDeps;
  takeSession(): SessionConfig | null;
  releaseSession(session: SessionConfig): Promise<boolean>;
  clearOwnedLive(sessionId: string): void;
  isReplacing(sessionId: string): boolean;
}

/**
 * Cancel the current call. A cancel can land after connect() resolved, so a
 * live session is taken and released too: leaving it admitted while its
 * transport is torn down would present a dead session as live to the next
 * connect().
 */
export async function cancelConnection(ctx: CancelContext): Promise<void> {
  const { state, deps } = ctx;
  state.connecting = false;
  state.voiceErrorCode = '';
  state.transportStatus = 'idle';
  state.agentState = null;
  const session = ctx.takeSession();
  if (session) ctx.clearOwnedLive(session.id);
  // Start the local teardown without awaiting it: media detach happens
  // synchronously inside cancelConnect(), while a stalled transport must not
  // gate the backend release (that would leak the admitted session).
  const localCancel = deps
    .getRuntime()
    .cancelConnect()
    .catch(() => {});
  if (!session) {
    await localCancel;
    return;
  }
  const releasedOk = await ctx.releaseSession(session);
  // A replacement can be live or still connecting while the release is in
  // flight. Never overwrite either with the failed old session.
  if (!releasedOk) applyFailedRelease(state, session, ctx.isReplacing(session.id));
  await localCancel;
}
