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

export type LinkResult = { kind: "linked" } | { kind: "error"; code: string } | { kind: "cancelled" } | { kind: "pending" };

/** The app URL the backend's Google link flow redirected back to: `?linked=google` or `?link_error=<code>`. */
export function parseLinkResult(url: string): LinkResult {
  const err = url.match(/[?#&]link_error=([^&#]+)/);
  if (err) return { kind: "error", code: decodeURIComponent(err[1]) };
  if (/[?#&]linked=google(?:[&#]|$)/.test(url)) return { kind: "linked" };
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
};

/** Translation key for a linking error code; anything unknown gets the generic failure. */
export function linkErrorKey(code: string | undefined | null): string {
  return (code && LINK_ERROR_KEYS[code]) || "account.linkFailed";
}
