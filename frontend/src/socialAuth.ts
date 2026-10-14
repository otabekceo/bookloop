/**
 * Sign in with Apple, native Google sign-in (Android) + linking Google/Apple to the signed-in account
 * (Settings -> Account).
 *
 * The app never decides who someone is: it only forwards what Apple/Google hand it (Apple's signed
 * identity token; Google's ID token from the native sheet, or the browser flow that runs entirely on the
 * backend) and the backend verifies it.
 */
import { Platform } from "react-native";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";
import * as AppleAuthentication from "expo-apple-authentication";
import * as Crypto from "expo-crypto";
import Constants, { ExecutionEnvironment } from "expo-constants";

import { ApiError, apiFetch } from "@/src/api";
import { parseLinkResult, type LinkResult } from "@/src/accountLinking";
import { nativeGoogleUsable, nativeOutcome, type NativeOutcome } from "@/src/googleNative";
import { beginOAuthAttempt, endOAuthAttempt, verifierFor } from "@/src/oauthPending";

export type AppleCredentialBody = {
  identity_token: string;
  nonce_id: string;
  given_name?: string | null;
  family_name?: string | null;
};

/** Sign in with Apple exists only on Apple devices (iOS 13+), and only when the server has it enabled. */
export async function appleAvailable(serverEnabled: boolean): Promise<boolean> {
  if (Platform.OS !== "ios" || !serverEnabled) return false;
  try {
    return await AppleAuthentication.isAvailableAsync();
  } catch {
    return false;
  }
}

/**
 * Runs the native Apple sheet. Returns null when the user cancelled. The nonce is issued by the server
 * (single use); Apple signs its SHA-256 into the token, so the token can't be replayed or swapped in.
 */
export async function appleCredential(): Promise<AppleCredentialBody | null> {
  const { nonce_id, nonce } = await apiFetch<{ nonce_id: string; nonce: string }>("/api/auth/apple/nonce", { method: "POST" });
  const hashed = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, nonce);
  try {
    const cred = await AppleAuthentication.signInAsync({
      requestedScopes: [AppleAuthentication.AppleAuthenticationScope.FULL_NAME, AppleAuthentication.AppleAuthenticationScope.EMAIL],
      nonce: hashed,
    });
    if (!cred.identityToken) throw new Error("no identity token");
    // The name comes only on the first authorization and only here (never in the token).
    return {
      identity_token: cred.identityToken,
      nonce_id,
      given_name: cred.fullName?.givenName ?? null,
      family_name: cred.fullName?.familyName ?? null,
    };
  } catch (e: any) {
    if (e?.code === "ERR_REQUEST_CANCELED") return null;
    throw e;
  }
}

export type GoogleNativeBody = { id_token: string; nonce_id: string };

/**
 * The backend's Google OAuth client of type "Web application" (Google Cloud Console -> Credentials). Native
 * sign-in on Android asks Google for an ID token issued TO this client (`aud`), which is what the backend
 * checks; the Android OAuth client (package + signing SHA-1) is never named in the app, Google matches it
 * from the installed app itself. A client ID is a public identifier, not a secret.
 */
const GOOGLE_WEB_CLIENT_ID = (process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID ?? "").trim();

/** Native Google sign-in (Android Credential Manager), when the server has it on and this build is set up.
 * Never in Expo Go, which doesn't contain the native module. */
export function googleNativeAvailable(serverNative: boolean): boolean {
  if (Constants.executionEnvironment === ExecutionEnvironment.StoreClient) return false;
  return nativeGoogleUsable({ platform: Platform.OS, serverNative, webClientId: GOOGLE_WEB_CLIENT_ID });
}

async function nativeGoogleResult(nonce: string): Promise<NativeOutcome> {
  try {
    // Loaded only here, on Android: the module talks to native code as soon as it is imported.
    const { GoogleOneTapSignIn } = await import("react-native-nitro-google-signin");
    // Offers to install/update Google Play services when that is what's missing.
    await GoogleOneTapSignIn.checkPlayServices(true);
    // Google signs this exact nonce into the ID token; the backend accepts the token only together with
    // this nonce's id, once. No offline access: the app never receives a code to trade for Google tokens.
    GoogleOneTapSignIn.configure({ webClientId: GOOGLE_WEB_CLIENT_ID, nonce, offlineAccess: false });
    return nativeOutcome(await GoogleOneTapSignIn.presentExplicitSignIn());
  } catch (e) {
    return nativeOutcome(undefined, e);
  }
}

/**
 * Runs Google's native account sheet. Returns null when the user cancelled; throws an ApiError with code
 * `google_unavailable` (no Play services / Google account) or `google_failed` otherwise. The ID token is
 * only passed on to the backend, never read or trusted here.
 */
export async function googleNativeCredential(): Promise<GoogleNativeBody | null> {
  const { nonce_id, nonce } = await apiFetch<{ nonce_id: string; nonce: string }>("/api/auth/google/nonce", { method: "POST" });
  const outcome = await nativeGoogleResult(nonce);
  switch (outcome.kind) {
    case "success":
      return { id_token: outcome.idToken, nonce_id };
    case "cancelled":
      return null;
    case "unavailable":
      throw new ApiError("Google sign-in is not available on this device", 0, "google_unavailable");
    case "misconfigured":
      if (__DEV__) {
        console.warn("Google sign-in: DEVELOPER_ERROR — check the Android OAuth client (package name + SHA-1 of the signing key) and that EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID is the Web client ID.");
      }
      throw new ApiError("Google sign-in failed", 0, "google_failed");
    default:
      throw new ApiError("Google sign-in failed", 0, "google_failed");
  }
}

/**
 * Links a Google account to the signed-in account through the backend's Google flow. Google's return
 * only parks the identity; finishGoogleLink() then confirms it with this attempt's verifier.
 */
export async function linkGoogle(): Promise<LinkResult> {
  const redirect = Platform.OS === "web" && typeof window !== "undefined" ? window.location.origin + "/settings/account" : Linking.createURL("settings/account");
  const { challenge, attempt } = await beginOAuthAttempt("link");
  let url: string;
  try {
    ({ url } = await apiFetch<{ url: string }>("/api/auth/google/link", {
      method: "POST",
      body: { redirect, code_challenge: challenge, code_challenge_method: "S256" },
    }));
  } catch (e) {
    await endOAuthAttempt("link", attempt);
    throw e;
  }
  if (Platform.OS === "web") {
    // The page comes back to /settings/account?link_id=…, which finishes the link (see that screen).
    window.location.href = url;
    return { kind: "pending" };
  }
  const result = await WebBrowser.openAuthSessionAsync(url, redirect);
  if (result.type !== "success" || !(result as any).url) {
    await endOAuthAttempt("link", attempt);
    return { kind: "cancelled" };
  }
  return finishGoogleLink((result as any).url);
}

// One confirmation per link id: the browser result and the deep link that reopened Settings can both
// deliver the same return, and both callers must see the same outcome.
const linkInflight = new Map<string, Promise<LinkResult>>();

/**
 * Finishes a Link Google return (from linkGoogle(), or from Settings -> Account opened by the redirect
 * after the app was restarted). Returns that the user cancelled for anything this app didn't start.
 */
export function finishGoogleLink(url: string): Promise<LinkResult> {
  const r = parseLinkResult(url);
  if (r.kind === "error") {
    return (async (): Promise<LinkResult> => {
      if (!(await verifierFor("link", r.attempt ?? null))) return { kind: "cancelled" };
      await endOAuthAttempt("link", r.attempt);
      return r;
    })();
  }
  if (r.kind !== "confirm") return Promise.resolve(r);
  const existing = linkInflight.get(r.linkId);
  if (existing) return existing;
  const p = (async (): Promise<LinkResult> => {
    const verifier = await verifierFor("link", r.attempt);
    if (!verifier) return { kind: "cancelled" };
    try {
      await apiFetch("/api/auth/google/link/complete", { method: "POST", body: { link_id: r.linkId, code_verifier: verifier } });
      return { kind: "linked" };
    } catch (e: any) {
      return { kind: "error", code: e?.code || "link_failed" };
    } finally {
      await endOAuthAttempt("link", r.attempt);
    }
  })();
  linkInflight.set(r.linkId, p);
  return p;
}
