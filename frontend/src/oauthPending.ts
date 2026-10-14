/**
 * The app's pending Google sign-in / Link Google attempts (one of each at most). The verifier stays on
 * this device; see src/pkce.ts for how it binds the backend's one-time tickets to this attempt.
 *
 * Kept in memory and in SecureStore: if Android kills the app while the browser is open, Google's
 * redirect relaunches it and the cold-start path (auth bootstrap / Settings -> Account) still finds the
 * verifier. Every attempt is forgotten once it completes, fails or is cancelled, and expires on its own.
 */
import * as Crypto from "expo-crypto";

import { storage } from "@/src/utils/storage";
import {
  attemptOf,
  base64ToBase64Url,
  base64UrlEncode,
  parsePending,
  pendingFor,
  serializePending,
  type OAuthKind,
  type PendingOAuth,
} from "@/src/pkce";

const KEYS: Record<OAuthKind, string> = {
  login: "bookloop_oauth_pending_login",
  link: "bookloop_oauth_pending_link",
};

// undefined = not loaded from SecureStore yet; null = no pending attempt.
const mem: Partial<Record<OAuthKind, PendingOAuth | null>> = {};

async function load(kind: OAuthKind): Promise<PendingOAuth | null> {
  if (mem[kind] === undefined) {
    const stored = parsePending(await storage.secureGet<string | null>(KEYS[kind], null));
    if (mem[kind] === undefined) mem[kind] = stored; // a begin/end that ran meanwhile wins
  }
  return mem[kind] ?? null;
}

/** Starts a new attempt: a fresh verifier replaces any earlier attempt of the same kind, whose
 * redirect — if it still arrives — is then ignored. Returns what the backend gets to see. */
export async function beginOAuthAttempt(kind: OAuthKind): Promise<{ challenge: string; attempt: string }> {
  const verifier = base64UrlEncode(await Crypto.getRandomBytesAsync(32));
  const digest = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, verifier, {
    encoding: Crypto.CryptoEncoding.BASE64,
  });
  const challenge = base64ToBase64Url(digest);
  const pending: PendingOAuth = { kind, verifier, attempt: attemptOf(challenge), createdAt: Date.now() };
  mem[kind] = pending;
  await storage.secureSet(KEYS[kind], serializePending(pending));
  return { challenge, attempt: pending.attempt };
}

/** The verifier for a redirect of `kind` carrying `attempt` — only if that is the current, unexpired
 * attempt. Null for anything else (a stale attempt, a link this app never started). */
export async function verifierFor(kind: OAuthKind, attempt: string | null): Promise<string | null> {
  return pendingFor(await load(kind), kind, attempt, Date.now())?.verifier ?? null;
}

/** Forgets the attempt (completed, failed or cancelled). With `attempt`, only while it is still the
 * current one, so finishing an old attempt never wipes a newer one. */
export async function endOAuthAttempt(kind: OAuthKind, attempt?: string | null): Promise<void> {
  const current = await load(kind);
  if (attempt && current && current.attempt !== attempt) return;
  mem[kind] = null;
  await storage.secureRemove(KEYS[kind]);
}
