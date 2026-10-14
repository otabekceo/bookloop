/**
 * Pure helpers for native Google sign-in on Android (Credential Manager), with no React/Expo imports so
 * plain Node can unit-test them. The app never decides who someone is: it hands Google's ID token, with
 * the single-use nonce the server issued for it, to the backend, which verifies everything.
 */

/** A Google OAuth client ID ("<project number>-<id>.apps.googleusercontent.com"). A public identifier. */
const CLIENT_ID_RE = /^\d+-[a-z0-9]+\.apps\.googleusercontent\.com$/;

export function isGoogleClientId(id: string | null | undefined): boolean {
  return CLIENT_ID_RE.test((id ?? "").trim());
}

/**
 * Native sign-in is used only on Android, only when the server has it switched on (`google_native` from
 * /api/auth/providers) and only when this build carries the Web client ID it needs. Anything else keeps the
 * browser flow.
 */
export function nativeGoogleUsable(o: { platform: string; serverNative: boolean; webClientId: string | null | undefined }): boolean {
  return o.platform === "android" && o.serverNative && isGoogleClientId(o.webClientId);
}

export type NativeOutcome =
  | { kind: "success"; idToken: string }
  | { kind: "cancelled" }
  /** Google Play services missing/outdated, or no Google account on the device. */
  | { kind: "unavailable" }
  /** Wrong package/SHA-1 on the Android OAuth client, or the Android client ID used as the Web one. */
  | { kind: "misconfigured" }
  | { kind: "failed" };

/**
 * Maps what the native library returned (or threw) to what the app does next. Only a success with an ID
 * token continues to the backend; every other result ends the attempt without signing anyone in.
 */
export function nativeOutcome(response: unknown, error?: unknown): NativeOutcome {
  if (error !== undefined) {
    const code = (error as { code?: unknown } | null)?.code;
    if (code === "SIGN_IN_CANCELLED") return { kind: "cancelled" };
    if (code === "PLAY_SERVICES_NOT_AVAILABLE") return { kind: "unavailable" };
    if (code === "DEVELOPER_ERROR") return { kind: "misconfigured" };
    return { kind: "failed" };
  }
  const r = response as { type?: unknown; data?: { idToken?: unknown } | null } | null;
  if (r?.type === "success") {
    const token = r.data?.idToken;
    return typeof token === "string" && token.length > 0 ? { kind: "success", idToken: token } : { kind: "failed" };
  }
  if (r?.type === "cancelled") return { kind: "cancelled" };
  // The account sheet found no Google account to offer.
  if (r?.type === "noSavedCredentialFound") return { kind: "unavailable" };
  return { kind: "failed" };
}

const GOOGLE_ERROR_KEYS: Record<string, string> = {
  link_required: "auth.googleLinkRequired",
  account_mismatch: "auth.googleAccountMismatch",
  google_unavailable: "auth.googleUnavailable",
};

/** Translation key for a Google sign-in failure code (backend refusal or device problem). */
export function googleErrorKey(code: string | undefined | null): string {
  return (code && GOOGLE_ERROR_KEYS[code]) || "auth.googleFailed";
}
