/**
 * Pure helpers that bind a Google sign-in / Link Google redirect to the app attempt that started it
 * (PKCE-style, RFC 7636 S256). No React/Expo imports, so plain Node can unit-test them.
 *
 * The app keeps a random verifier to itself and sends only its SHA-256 (the challenge) to the backend.
 * The one-time ticket Google's redirect brings back only works together with that verifier, and the
 * redirect carries `attempt` (the challenge's first characters) so the app only ever pairs a redirect
 * with the attempt it belongs to — and ignores redirects it never started (a crafted link, an old tab).
 */

export type OAuthKind = "login" | "link";

export type PendingOAuth = { kind: OAuthKind; verifier: string; attempt: string; createdAt: number };

/** The backend accepts the browser's return for 10 minutes, then the ticket for 2 (login) or 5 (link). */
export const PENDING_OAUTH_MAX_AGE_MS = 15 * 60 * 1000;
export const ATTEMPT_CHARS = 16;
const VERIFIER_RE = /^[A-Za-z0-9._~-]{43,128}$/;
const ATTEMPT_RE = /^[A-Za-z0-9_-]{16}$/;

const B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

/** Unpadded base64url of raw bytes (a verifier made of 32 random bytes is 43 characters). */
export function base64UrlEncode(bytes: Uint8Array): string {
  let out = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | ((bytes[i + 1] ?? 0) << 8) | (bytes[i + 2] ?? 0);
    out += B64URL[(n >> 18) & 63] + B64URL[(n >> 12) & 63];
    if (i + 1 < bytes.length) out += B64URL[(n >> 6) & 63];
    if (i + 2 < bytes.length) out += B64URL[n & 63];
  }
  return out;
}

/** Standard (padded) base64 -> unpadded base64url, as the S256 challenge must be. */
export function base64ToBase64Url(b64: string): string {
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function attemptOf(challenge: string): string {
  return challenge.slice(0, ATTEMPT_CHARS);
}

function param(url: string, name: string): string | null {
  const m = url.match(new RegExp(`[?#&]${name}=([^&#]*)`));
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return null;
  }
}

export type OAuthReturn = {
  attempt: string | null;
  sessionId: string | null;
  googleError: string | null;
  linkId: string | null;
  linkError: string | null;
};

/** What a redirect back from the backend's Google flow carries (all null for any other URL). */
export function parseOAuthReturn(url: string): OAuthReturn {
  const attempt = param(url, "attempt");
  return {
    attempt: attempt && ATTEMPT_RE.test(attempt) ? attempt : null,
    sessionId: param(url, "session_id") || null,
    googleError: param(url, "google_error"),
    linkId: param(url, "link_id") || null,
    linkError: param(url, "link_error"),
  };
}

/** The pending attempt a redirect belongs to: same kind, same attempt, not expired — otherwise null. */
export function pendingFor(
  p: PendingOAuth | null,
  kind: OAuthKind,
  attempt: string | null,
  now: number,
): PendingOAuth | null {
  if (!p || !attempt || p.kind !== kind || p.attempt !== attempt) return null;
  // A clock that jumped backwards by more than a minute is treated as expired too.
  if (now - p.createdAt > PENDING_OAUTH_MAX_AGE_MS || p.createdAt - now > 60_000) return null;
  return p;
}

export function serializePending(p: PendingOAuth): string {
  return JSON.stringify(p);
}

/** A stored attempt, or null for anything missing, corrupted or of the wrong shape. */
export function parsePending(raw: unknown): PendingOAuth | null {
  if (typeof raw !== "string" || !raw) return null;
  try {
    const p = JSON.parse(raw);
    if (
      (p?.kind === "login" || p?.kind === "link") &&
      typeof p.verifier === "string" && VERIFIER_RE.test(p.verifier) &&
      typeof p.attempt === "string" && ATTEMPT_RE.test(p.attempt) &&
      typeof p.createdAt === "number" && Number.isFinite(p.createdAt)
    ) {
      return { kind: p.kind, verifier: p.verifier, attempt: p.attempt, createdAt: p.createdAt };
    }
  } catch {}
  return null;
}
