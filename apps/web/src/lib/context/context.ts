export type ContextKind = 'memory' | 'tool' | 'history';
export type ContextTrust = 'data' | 'conversation';

export interface ContextIncluded {
  id: string;
  kind: ContextKind;
  text: string;
  trust: ContextTrust;
}

export interface ContextExcluded {
  id: string;
  reason: string;
}

export interface ContextBudget {
  used_chars: number;
  limit_chars: number;
}

export interface ContextDiff {
  added: string[];
  removed: string[];
}

export interface TurnContext {
  turn_id: string;
  revision: number;
  prompt_sha256: string;
  history_count: number;
  included: ContextIncluded[];
  excluded: ContextExcluded[];
  budget: ContextBudget;
  previous_turn_id: string | null;
  diff: ContextDiff;
}

export interface ContextBlock {
  id: string;
  kind: string;
  role: string;
  text: string;
  truncated: boolean;
  estimated_tokens: number;
  state: string;
  reason: string | null;
}

export interface ContextBlocks {
  blocks: ContextBlock[];
  /** Present when the server also returns the character budget. */
  budget?: ContextBudget;
}

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export class ContextRequestError extends Error {
  constructor(
    code: string,
    readonly status = 0,
  ) {
    super(code);
    this.name = 'ContextRequestError';
  }
}

function invalid(path: string): ContextRequestError {
  return new ContextRequestError(`CONTEXT_INVALID:${path}`);
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid(path);
  return value as Record<string, unknown>;
}

function list(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) throw invalid(path);
  return value;
}

function text(value: unknown, path: string): string {
  if (typeof value !== 'string') throw invalid(path);
  return value;
}

function count(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw invalid(path);
  return value;
}

function flag(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') throw invalid(path);
  return value;
}

function parseBudget(value: unknown, path: string): ContextBudget {
  const item = record(value, path);
  return {
    used_chars: count(item.used_chars, `${path}.used_chars`),
    limit_chars: count(item.limit_chars, `${path}.limit_chars`),
  };
}

function parseIncluded(value: unknown, path: string): ContextIncluded {
  const item = record(value, path);
  const kind = text(item.kind, `${path}.kind`);
  const trust = text(item.trust, `${path}.trust`);
  if (kind !== 'memory' && kind !== 'tool' && kind !== 'history') throw invalid(`${path}.kind`);
  if (trust !== 'data' && trust !== 'conversation') throw invalid(`${path}.trust`);
  return { id: text(item.id, `${path}.id`), kind, text: text(item.text, `${path}.text`), trust };
}

/** Excluded entries intentionally carry only an id and a reason; any text is dropped. */
function parseExcluded(value: unknown, path: string): ContextExcluded {
  const item = record(value, path);
  return { id: text(item.id, `${path}.id`), reason: text(item.reason, `${path}.reason`) };
}

function stringList(value: unknown, path: string): string[] {
  return list(value, path).map((entry, index) => text(entry, `${path}.${index}`));
}

export function parseTurnContext(value: unknown): TurnContext {
  const item = record(value, 'context');
  const previous = item.previous_turn_id;
  if (previous !== null && previous !== undefined && typeof previous !== 'string') {
    throw invalid('context.previous_turn_id');
  }
  const diff = record(item.diff, 'context.diff');
  return {
    turn_id: text(item.turn_id, 'context.turn_id'),
    revision: count(item.revision, 'context.revision'),
    prompt_sha256: text(item.prompt_sha256, 'context.prompt_sha256'),
    history_count: count(item.history_count, 'context.history_count'),
    included: list(item.included, 'context.included').map((entry, index) =>
      parseIncluded(entry, `context.included.${index}`),
    ),
    excluded: list(item.excluded, 'context.excluded').map((entry, index) =>
      parseExcluded(entry, `context.excluded.${index}`),
    ),
    budget: parseBudget(item.budget, 'context.budget'),
    previous_turn_id: previous ?? null,
    diff: {
      added: stringList(diff.added, 'context.diff.added'),
      removed: stringList(diff.removed, 'context.diff.removed'),
    },
  };
}

function parseBlock(value: unknown, path: string): ContextBlock {
  const item = record(value, path);
  const reason = item.reason;
  if (reason !== null && reason !== undefined && typeof reason !== 'string') throw invalid(`${path}.reason`);
  return {
    id: text(item.id, `${path}.id`),
    kind: text(item.kind, `${path}.kind`),
    role: text(item.role, `${path}.role`),
    text: text(item.text, `${path}.text`),
    truncated: flag(item.truncated, `${path}.truncated`),
    estimated_tokens: count(item.estimated_tokens, `${path}.estimated_tokens`),
    state: text(item.state, `${path}.state`),
    reason: reason ?? null,
  };
}

export function parseContextBlocks(value: unknown): ContextBlocks {
  const item = record(value, 'blocks');
  const blocks = list(item.blocks, 'blocks.blocks').map((block, index) => parseBlock(block, `blocks.blocks.${index}`));
  const budget =
    item.budget === undefined || item.budget === null ? undefined : parseBudget(item.budget, 'blocks.budget');
  return { blocks, budget };
}

function detailCode(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null;
  const detail = (value as { detail?: unknown }).detail;
  if (!detail || typeof detail !== 'object') return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === 'string' && code ? code : null;
}

async function getJson(path: string, fetchImpl: FetchLike): Promise<unknown> {
  let response: Response;
  try {
    response = await fetchImpl('/api/v1/' + path);
  } catch {
    throw new ContextRequestError('API_UNAVAILABLE');
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ContextRequestError(response.ok ? 'API_RESPONSE_INVALID' : `HTTP_${response.status}`, response.status);
  }
  if (!response.ok) {
    throw new ContextRequestError(detailCode(data) ?? `HTTP_${response.status}`, response.status);
  }
  return data;
}

export async function loadTurnContext(
  conversationId: string,
  turnId: string,
  fetchImpl: FetchLike = fetch,
): Promise<TurnContext> {
  const path = `conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/context`;
  return parseTurnContext(await getJson(path, fetchImpl));
}

export async function loadContextBlocks(conversationId: string, fetchImpl: FetchLike = fetch): Promise<ContextBlocks> {
  const path = `conversations/${encodeURIComponent(conversationId)}/context/blocks?limit=100`;
  return parseContextBlocks(await getJson(path, fetchImpl));
}

export function isContextNotFound(error: unknown): boolean {
  return error instanceof ContextRequestError && error.status === 404;
}

export function friendlyReceiptError(): string {
  return 'The context receipt could not be loaded. Retry when the connection is back.';
}

export function friendlyBlocksError(): string {
  return 'The compiled context could not be read. Your saved facts are still listed.';
}

export * from './mind';
