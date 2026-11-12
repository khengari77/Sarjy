/**
 * Microphone permission is requested before a voice session is created so a
 * denial cannot consume a session slot or start a room with no capture.
 * The probe stream is stopped immediately; LiveKit opens its own track later.
 */
export async function ensureMicrophoneAccess(): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new MicrophoneError('MICROPHONE_UNAVAILABLE');
  }
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    throw new MicrophoneError(microphoneErrorCode(error));
  }
  for (const track of stream.getTracks()) track.stop();
}

export class MicrophoneError extends Error {
  constructor(readonly code: MicrophoneErrorCode) {
    super(code);
  }
}

export type MicrophoneErrorCode =
  | 'MICROPHONE_PERMISSION_DENIED'
  | 'MICROPHONE_NOT_FOUND'
  | 'MICROPHONE_UNAVAILABLE';

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
