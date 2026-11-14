/**
 * Owns the one short-lived connecting capture.
 *
 * The controller exists so the connection path never holds analyser, timer and
 * capture teardown inline: there is exactly one preview per attempt, and every
 * exit path calls `stop()`.
 */

import type { ConnectingPreview } from './microphonePreview';
import { openConnectingPreview } from './microphonePreview';

export interface ConnectingPreviewController {
  /**
   * Open the capture and start publishing levels into `levels.micLevel`.
   * Throws the microphone error untouched; returns false when the attempt was
   * superseded while the browser was deciding.
   */
  start(isCurrent: () => boolean): Promise<boolean>;
  stop(): void;
}

export function createConnectingPreview(levels: { micLevel: number }): ConnectingPreviewController {
  let preview: ConnectingPreview | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;

  function stop() {
    if (timer !== null) clearInterval(timer);
    timer = null;
    preview?.stop();
    preview = null;
    levels.micLevel = 0;
  }

  async function start(isCurrent: () => boolean): Promise<boolean> {
    const opened = await openConnectingPreview();
    if (!isCurrent()) {
      opened.stop();
      return false;
    }
    preview = opened;
    timer = setInterval(() => {
      if (preview) levels.micLevel = preview.level();
    }, 80);
    return true;
  }

  return { start, stop };
}

/**
 * Connection-facing wrapper: a permission failure becomes a typed connection
 * error and always tears the preview down. Returns false when the attempt must
 * stop (denied, or superseded while permission was pending).
 */
export async function startConnectingPreview(
  controller: ConnectingPreviewController,
  isCurrent: () => boolean,
  onError: (error: unknown) => void,
): Promise<boolean> {
  try {
    return await controller.start(isCurrent);
  } catch (error) {
    controller.stop();
    onError(error);
    return false;
  }
}
