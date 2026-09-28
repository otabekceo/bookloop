/** Chat message list helpers (kept free of React/Expo imports so they can be unit-tested with plain Node). */

export type ChatMessage = { id: string; created_at?: string | null; client_id?: string | null; [k: string]: any };

/** A message typed on this device that the server hasn't confirmed yet (or that failed to send). */
export type OutboxItem = { client_id: string; text: string; status: "sending" | "failed"; created_at: string };

/** Adds/updates messages by id, keeping time order. Poll results, send responses and the anchor
 * message that incremental polls repeat all go through here, so nothing is ever shown twice. */
export function mergeMessages<T extends ChatMessage>(current: T[], incoming: T[]): T[] {
  if (!incoming.length) return current;
  const byId = new Map(current.map((m) => [m.id, m]));
  let appended = false;
  for (const m of incoming) {
    if (!byId.has(m.id)) appended = true;
    byId.set(m.id, m);
  }
  const merged = Array.from(byId.values());
  if (appended) merged.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  return merged;
}

/** Server messages followed by the not-yet-confirmed ones. An outbox item is hidden as soon as its
 * server copy (same client_id) is present, whichever arrives first: the send response or a poll. */
export function withOutbox<T extends ChatMessage>(server: T[], outbox: OutboxItem[], senderId: string | undefined) {
  const confirmed = new Set(server.map((m) => m.client_id).filter(Boolean));
  const pending = outbox
    .filter((m) => !confirmed.has(m.client_id))
    .map((m) => ({ id: m.client_id, type: "text", sender_id: senderId, text: m.text, created_at: m.created_at, _outbox: m }));
  return pending.length ? [...server, ...pending] : server;
}

export const newClientId = () => `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
