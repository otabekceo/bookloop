/**
 * Pure account-linking helpers (no React/Expo imports, so plain Node can unit-test them).
 * The server decides everything; these only interpret its answers for the UI.
 */

export type Provider = "google" | "apple";

export type Identities = {
  email: string | null;
  password: boolean;
  providers: Record<Provider, { linked: boolean; email: string | null; private_email: boolean }>;
  available: Record<Provider, boolean>;
};

export type LinkResult =
  | { kind: "linked" }
  | { kind: "error"; code: string; attempt?: string | null }
  | { kind: "cancelled" }
  | { kind: "pending" }
  /** Google came back: the app still has to confirm the link with its verifier (POST /link/complete). */
  | { kind: "confirm"; linkId: string; attempt: string };

function param(url: string, name: string): string | null {
  const m = url.match(new RegExp(`[?#&]${name}=([^&#]+)`));
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return null;
  }
}

/** The app URL the backend's Google link flow redirected back to: `?link_id=<id>&attempt=<a>` (to be
 * confirmed) or `?link_error=<code>&attempt=<a>`. A URL never says "linked" by itself: only the
 * server's answer to the confirmation does. */
export function parseLinkResult(url: string): LinkResult {
  const attempt = param(url, "attempt");
  const err = param(url, "link_error");
  if (err) return { kind: "error", code: err, attempt };
  const linkId = param(url, "link_id");
  if (linkId && attempt) return { kind: "confirm", linkId, attempt };
  return { kind: "cancelled" };
}

/** How many ways this account can sign in. */
export function loginMethodCount(ids: Pick<Identities, "password" | "providers">): number {
  return (ids.password ? 1 : 0) + (Object.values(ids.providers) as { linked: boolean }[]).filter((p) => p.linked).length;
}

/** Unlinking is offered only when another way to sign in remains (the server enforces this too). */
export function canUnlink(ids: Pick<Identities, "password" | "providers">, provider: Provider): boolean {
  return ids.providers[provider]?.linked === true && loginMethodCount(ids) > 1;
}

const LINK_ERROR_KEYS: Record<string, string> = {
  identity_in_use: "account.errorInUse",
  provider_already_linked: "account.errorAlreadyLinked",
  last_login_method: "account.errorLastMethod",
  // Native Google (Android): no Google Play services / Google account on the device.
  google_unavailable: "auth.googleUnavailable",
};

/** Translation key for a linking error code; anything unknown gets the generic failure. */
export function linkErrorKey(code: string | undefined | null): string {
  return (code && LINK_ERROR_KEYS[code]) || "account.linkFailed";
}
