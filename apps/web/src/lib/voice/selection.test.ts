import { describe, expect, it, vi } from 'vitest';
import type { ConversationStore } from './conversations';
import { initialVoiceState, type VoiceState } from './connection';
import { createSelectionController } from './selection';

function conversationStore(createChat: () => Promise<{ id: string; title: string; revision: number }>) {
  const select = vi.fn(async () => {});
  return {
    store: { state: { current: 'c1' }, createChat, select } as unknown as ConversationStore,
    select,
  };
}

function harness(
  createChat: () => Promise<{ id: string; title: string; revision: number }>,
  voice: VoiceState = initialVoiceState(),
) {
  const { store, select } = conversationStore(createChat);
  const selection = createSelectionController({
    voice,
    getPending: () => null,
    setPending: () => {},
    // The page's run catches failures into the normal list error.
    run: async (work) => {
      try {
        await work();
      } catch {
        /* listError is set by the shell in production */
      }
    },
    cancelConnect: async () => {},
    endVoice: async () => {},
    getConversations: () => store,
  });
  return { selection, select };
}

describe('create', () => {
  it('returns true after creating and selecting a chat', async () => {
    const { selection, select } = harness(async () => ({ id: 'c-new', title: 'New conversation', revision: 0 }));

    await expect(selection.create()).resolves.toBe(true);
    expect(select).toHaveBeenCalledWith('c-new');
  });

  it('returns false without selecting when creation fails', async () => {
    const { selection, select } = harness(async () => {
      throw new Error('CONVERSATION_CREATE_FAILED');
    });

    await expect(selection.create()).resolves.toBe(false);
    expect(select).not.toHaveBeenCalled();
  });
});
