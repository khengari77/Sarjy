import { afterEach, describe, expect, it, vi } from 'vitest';
import { ensureMicrophoneAccess, MicrophoneError, microphoneErrorCode } from './microphone';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('microphone error mapping', () => {
  it('maps browser permission names to actionable codes', () => {
    expect(microphoneErrorCode({ name: 'NotAllowedError' })).toBe('MICROPHONE_PERMISSION_DENIED');
    expect(microphoneErrorCode({ name: 'SecurityError' })).toBe('MICROPHONE_PERMISSION_DENIED');
    expect(microphoneErrorCode({ name: 'NotFoundError' })).toBe('MICROPHONE_NOT_FOUND');
    expect(microphoneErrorCode({ name: 'OverconstrainedError' })).toBe('MICROPHONE_NOT_FOUND');
    expect(microphoneErrorCode(new Error('boom'))).toBe('MICROPHONE_UNAVAILABLE');
  });
});

describe('microphone preflight', () => {
  it('releases the probe stream immediately', async () => {
    const stop = vi.fn();
    const stream = { getTracks: () => [{ stop }] } as unknown as MediaStream;
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });

    await ensureMicrophoneAccess();

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it('surfaces a typed denial instead of a raw browser error', async () => {
    const getUserMedia = vi.fn().mockRejectedValue({ name: 'NotAllowedError' });
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });

    await expect(ensureMicrophoneAccess()).rejects.toBeInstanceOf(MicrophoneError);
    await expect(ensureMicrophoneAccess()).rejects.toMatchObject({ code: 'MICROPHONE_PERMISSION_DENIED' });
  });

  it('fails closed when the browser exposes no capture API', async () => {
    vi.stubGlobal('navigator', {});
    await expect(ensureMicrophoneAccess()).rejects.toMatchObject({ code: 'MICROPHONE_UNAVAILABLE' });
  });
});
