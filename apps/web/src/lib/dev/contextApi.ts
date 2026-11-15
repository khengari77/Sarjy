/**
 * Read-only client for the context diagnostics endpoints.
 *
 * Only GET requests are issued:
 *   GET /v1/conversations/{id}/context
 *   GET /v1/conversations/{id}/context/operations
 *
 * Responses are mapped field by field into the small view types below. Fields
 * the panel does not need (idempotency keys, proposal internals, any provider
 * metadata) are dropped rather than forwarded to the DOM.
 */

import type { DevAccess } from './mode';

export type DevReadErrorCode = 'DEV_CONTEXT_UNAUTHORIZED' | 'DEV_CONTEXT_UNAVAILABLE';

export class DevReadError extends Error {
  readonly code: DevReadErrorCode;

  constructor(code: DevReadErrorCode) {
    super(code);
    this.name = 'DevReadError';
    this.code = code;
  }
}

export interface DevDiff {
  added: string[];
  removed: string[];
  kept: string[];
  revisionBefore: number;
  revisionAfter: number;
}

export interface DevContextSnapshot {
  conversationId: string;
  revision: number;
  excludedIds: string[];
}

export interface DevOperation {
  id: string;
  commandType: string;
  outcome: string;
  revisionBefore: number | null;
  revisionAfter: number | null;
  createdAt: string;
  diff: DevDiff | null;
}

export interface DevOperationList {
  conversationId: string;
  revision: number;
  operations: DevOperation[];
}

export type DevFetcher = (input: string, init?: RequestInit) => Promise<Response>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function integer(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

function optionalInteger(value: unknown): number | null {
  return value === null || value === undefined ? null : integer(value);
}

function stringList(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  if (!value.every((entry) => typeof entry === 'string')) return null;
  return [...value];
}

async function getJson(fetcher: DevFetcher, path: string): Promise<unknown> {
  let response: Response;
  try {
    response = await fetcher(path, {
      method: 'GET',
      headers: { accept: 'application/json' },
      credentials: 'same-origin',
    });
  } catch {
    throw new DevReadError('DEV_CONTEXT_UNAVAILABLE');
  }
  // 401/403 are the server's authorization answers. A 404 means the
  // conversation is missing or not owned by this identity; treat it the same
  // way so the panel never reveals another account's conversation.
  if (response.status === 401 || response.status === 403 || response.status === 404) {
    throw new DevReadError('DEV_CONTEXT_UNAUTHORIZED');
  }
  if (!response.ok) throw new DevReadError('DEV_CONTEXT_UNAVAILABLE');
  try {
    return await response.json();
  } catch {
    throw new DevReadError('DEV_CONTEXT_UNAVAILABLE');
  }
}

export function mapContextPayload(value: unknown): DevContextSnapshot {
  if (!isRecord(value)) throw new DevReadError('DEV_CONTEXT_UNAVAILABLE');
  const conversationId = text(value.conversation_id);
  const revision = integer(value.revision);
  const excludedIds = stringList(value.excluded_ids);
  if (!conversationId || revision === null || !excludedIds) {
    throw new DevReadError('DEV_CONTEXT_UNAVAILABLE');
  }
  return { conversationId, revision, excludedIds };
}

function mapDiff(value: unknown): DevDiff | null {
  if (!isRecord(value)) return null;
  const added = stringList(value.added);
  const removed = stringList(value.removed);
  const kept = stringList(value.kept);
  const revisionBefore = integer(value.revision_before);
  const revisionAfter = integer(value.revision_after);
  if (!added || !removed || !kept || revisionBefore === null || revisionAfter === null) return null;
  return { added, removed, kept, revisionBefore, revisionAfter };
}

export function mapOperationPayload(value: unknown): DevOperation {
  if (!isRecord(value)) throw new DevReadError('DEV_CONTEXT_UNAVAILABLE');
  const id = text(value.id);
  const commandType = text(value.command_type);
  const outcome = text(value.outcome);
  const createdAt = text(value.created_at);
  if (!id || !commandType || !outcome || !createdAt) {
    throw new DevReadError('DEV_CONTEXT_UNAVAILABLE');
  }
  return {
    id,
    commandType,
    outcome,
    revisionBefore: optionalInteger(value.revision_before),
    revisionAfter: optionalInteger(value.revision_after),
    createdAt,
    diff: mapDiff(value.diff),
  };
}

export function mapOperationListPayload(value: unknown): DevOperationList {
  if (!isRecord(value)) throw new DevReadError('DEV_CONTEXT_UNAVAILABLE');
  const conversationId = text(value.conversation_id);
  const revision = integer(value.revision);
  if (!conversationId || revision === null || !Array.isArray(value.operations)) {
    throw new DevReadError('DEV_CONTEXT_UNAVAILABLE');
  }
  return {
    conversationId,
    revision,
    operations: value.operations.map(mapOperationPayload),
  };
}

function contextPath(conversationId: string): string {
  return `/api/v1/conversations/${encodeURIComponent(conversationId)}/context`;
}

export async function fetchDevContext(fetcher: DevFetcher, conversationId: string): Promise<DevContextSnapshot> {
  return mapContextPayload(await getJson(fetcher, contextPath(conversationId)));
}

export async function fetchDevOperations(fetcher: DevFetcher, conversationId: string): Promise<DevOperationList> {
  return mapOperationListPayload(await getJson(fetcher, `${contextPath(conversationId)}/operations`));
}

/**
 * The developer gate. It asks the server for the same read-only context a
 * developer would inspect; a rejected read means no developer controls are
 * rendered. This is an authorization check, not CSS visibility.
 */
export async function authorizeDeveloper(fetcher: DevFetcher, conversationId: string | null): Promise<DevAccess> {
  if (!conversationId) return 'unavailable';
  try {
    await fetchDevContext(fetcher, conversationId);
    return 'authorized';
  } catch (error) {
    if (error instanceof DevReadError && error.code === 'DEV_CONTEXT_UNAUTHORIZED') return 'unauthorized';
    return 'unavailable';
  }
}
