import { describe, expect, it, vi } from 'vitest';
import {
  DevReadError,
  authorizeDeveloper,
  fetchDevContext,
  fetchDevOperations,
  mapContextPayload,
  mapOperationListPayload,
  type DevFetcher,
} from './contextApi';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function recorder(response: Response | (() => Promise<Response>)) {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const fetcher: DevFetcher = async (url, init) => {
    calls.push({ url, init });
    return typeof response === 'function' ? response() : response;
  };
  return { calls, fetcher };
}

const contextPayload = { conversation_id: 'c1', revision: 4, excluded_ids: ['m2'] };

describe('read-only context client', () => {
  it('reads context with GET only', async () => {
    const { calls, fetcher } = recorder(jsonResponse(contextPayload));
    await expect(fetchDevContext(fetcher, 'c 1')).resolves.toEqual({
      conversationId: 'c1',
      revision: 4,
      excludedIds: ['m2'],
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('/api/v1/conversations/c%201/context');
    expect(calls[0].init?.method).toBe('GET');
  });

  it('reads operations with GET only and maps diffs', async () => {
    const { calls, fetcher } = recorder(
      jsonResponse({
        conversation_id: 'c1',
        revision: 5,
        operations: [
          {
            id: 'op1',
            command_type: 'exclude',
            outcome: 'applied',
            revision_before: 4,
            revision_after: 5,
            idempotency_key: 'secret-key-should-not-be-forwarded',
            created_at: '2026-11-14T09:00:00Z',
            diff: { added: [], removed: ['m2'], kept: ['m1'], revision_before: 4, revision_after: 5 },
            provider_key: 'sk-live-123',
          },
        ],
      }),
    );
    const result = await fetchDevOperations(fetcher, 'c1');
    expect(calls[0].url).toBe('/api/v1/conversations/c1/context/operations');
    expect(calls[0].init?.method).toBe('GET');
    expect(result.operations).toEqual([
      {
        id: 'op1',
        commandType: 'exclude',
        outcome: 'applied',
        revisionBefore: 4,
        revisionAfter: 5,
        createdAt: '2026-11-14T09:00:00Z',
        diff: { added: [], removed: ['m2'], kept: ['m1'], revisionBefore: 4, revisionAfter: 5 },
      },
    ]);
    expect(JSON.stringify(result)).not.toContain('secret-key-should-not-be-forwarded');
    expect(JSON.stringify(result)).not.toContain('sk-live-123');
  });

  it('maps 401, 403 and 404 to an unauthorized diagnostic error', async () => {
    for (const status of [401, 403, 404]) {
      const { fetcher } = recorder(jsonResponse({ detail: { code: 'NOPE' } }, status));
      await expect(fetchDevContext(fetcher, 'c1')).rejects.toMatchObject({ code: 'DEV_CONTEXT_UNAUTHORIZED' });
    }
  });

  it('maps server, network and malformed responses to unavailable', async () => {
    const serverError = recorder(jsonResponse({}, 500));
    await expect(fetchDevContext(serverError.fetcher, 'c1')).rejects.toMatchObject({ code: 'DEV_CONTEXT_UNAVAILABLE' });

    const network = recorder(async () => {
      throw new Error('offline');
    });
    await expect(fetchDevContext(network.fetcher, 'c1')).rejects.toBeInstanceOf(DevReadError);

    const malformed = recorder(jsonResponse({ conversation_id: 'c1', revision: '4', excluded_ids: [] }));
    await expect(fetchDevContext(malformed.fetcher, 'c1')).rejects.toMatchObject({ code: 'DEV_CONTEXT_UNAVAILABLE' });
  });

  it('rejects partial payloads instead of fabricating missing fields', () => {
    expect(() => mapContextPayload({ conversation_id: 'c1', revision: 1 })).toThrow(DevReadError);
    expect(() => mapOperationListPayload({ conversation_id: 'c1', revision: 1, operations: [{}] })).toThrow(
      DevReadError,
    );
  });

  it('authorizes from a successful read and treats missing ids as unavailable', async () => {
    const authorized = recorder(jsonResponse(contextPayload));
    await expect(authorizeDeveloper(authorized.fetcher, 'c1')).resolves.toBe('authorized');

    const denied = recorder(jsonResponse({}, 403));
    await expect(authorizeDeveloper(denied.fetcher, 'c1')).resolves.toBe('unauthorized');

    const missing = recorder(jsonResponse({}, 500));
    await expect(authorizeDeveloper(missing.fetcher, 'c1')).resolves.toBe('unavailable');

    const noId = recorder(jsonResponse(contextPayload));
    await expect(authorizeDeveloper(noId.fetcher, null)).resolves.toBe('unavailable');
    expect(noId.calls).toHaveLength(0);
  });

  it('never issues a write while authorizing or reading', async () => {
    const requests = vi.fn(async () => jsonResponse(contextPayload));
    await fetchDevContext(requests, 'c1');
    await fetchDevOperations(requests, 'c1').catch(() => undefined);
    await authorizeDeveloper(requests, 'c1');
    for (const call of requests.mock.calls) {
      const init = (call as unknown as [string, RequestInit?])[1];
      expect(init?.method ?? 'GET').toBe('GET');
    }
  });
});
