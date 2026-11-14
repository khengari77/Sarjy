/**
 * Microphone permission is requested before a voice session is created so a
 * denial cannot consume a session slot or start a room with no capture.
 *
 * The capture is a one-shot handle: callers either release it immediately
 * (`ensureMicrophoneAccess`, the preflight used before creating a session) or
 * keep it for the connecting preview and release it when the session takes
 * over. No caller may open a second stream for the same attempt.
 */

export interface MicrophoneCapture {
  stream: MediaStream;
  /** Stop every track exactly once; safe to call repeatedly. */
  stop(): void;
}

export async function acquireMicrophone(): Promise<MicrophoneCapture> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new MicrophoneError('MICROPHONE_UNAVAILABLE');
  }
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    throw new MicrophoneError(microphoneErrorCode(error));
  }
  let released = false;
  return {
    stream,
    stop() {
      if (released) return;
      released = true;
      for (const track of stream.getTracks()) track.stop();
    },
  };
}

/**
 * The preflight used by the connection path: request permission, then release
 * the probe stream immediately. LiveKit opens its own track later.
 */
export async function ensureMicrophoneAccess(): Promise<void> {
  const capture = await acquireMicrophone();
  capture.stop();
}

export class MicrophoneError extends Error {
  constructor(readonly code: MicrophoneErrorCode) {
    super(code);
  }
}

export type MicrophoneErrorCode = 'MICROPHONE_PERMISSION_DENIED' | 'MICROPHONE_NOT_FOUND' | 'MICROPHONE_UNAVAILABLE';

export function microphoneErrorCode(error: unknown): MicrophoneErrorCode {
  const name = (error as { name?: string } | null)?.name;
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'MICROPHONE_PERMISSION_DENIED';
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'MICROPHONE_NOT_FOUND';
    default:
      return 'MICROPHONE_UNAVAILABLE';
  }
}
