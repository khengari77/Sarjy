import { afterEach, describe, expect, it, vi } from 'vitest';
import { openConnectingPreview } from './microphonePreview';

function stubMicrophone() {
  const stop = vi.fn();
  const track = { stop };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
  const getUserMedia = vi.fn().mockResolvedValue(stream);
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
  return { stop, getUserMedia, stream };
}

function stubSilentAudio() {
  class AudioContext {
    createMediaStreamSource() {
      return { connect: vi.fn(), disconnect: vi.fn() };
    }
    createAnalyser() {
      return {
        fftSize: 512,
        smoothingTimeConstant: 0.6,
        getFloatTimeDomainData: (samples: Float32Array) => samples.fill(0.25),
        connect: vi.fn(),
        disconnect: vi.fn(),
      };
    }
    close = vi.fn(async () => {});
  }
  vi.stubGlobal('window', { AudioContext });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('connecting microphone preview', () => {
  it('requests permission once and meters the capture while connecting', async () => {
    const { getUserMedia } = stubMicrophone();
    stubSilentAudio();
    const preview = await openConnectingPreview();

    expect(getUserMedia).toHaveBeenCalledExactlyOnceWith({ audio: true });
    expect(preview.level()).toBeGreaterThan(0);
    preview.stop();
  });

  it('stops the analyser and every capture track, then reads zero', async () => {
    const { stop } = stubMicrophone();
    stubSilentAudio();
    const preview = await openConnectingPreview();

    preview.stop();
    preview.stop();

    expect(stop).toHaveBeenCalledTimes(1);
    expect(preview.level()).toBe(0);
  });

  it('surfaces a permission denial instead of leaking a preview', async () => {
    const getUserMedia = vi.fn().mockRejectedValue({ name: 'NotAllowedError' });
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
    await expect(openConnectingPreview()).rejects.toMatchObject({ code: 'MICROPHONE_PERMISSION_DENIED' });
  });
});
