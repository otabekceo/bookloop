/**
 * Sign in with Apple + linking Google/Apple to the signed-in account (Settings -> Account).
 *
 * The app never decides who someone is: it only forwards what Apple/Google hand it (Apple's signed
 * identity token; Google's flow runs entirely on the backend) and the backend verifies it.
 */
import { Platform } from "react-native";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";
import * as AppleAuthentication from "expo-apple-authentication";
import * as Crypto from "expo-crypto";

import { apiFetch } from "@/src/api";
import { parseLinkResult, type LinkResult } from "@/src/accountLinking";

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

/** Links a Google account to the signed-in account through the backend's Google flow. */
export async function linkGoogle(): Promise<LinkResult> {
  const redirect = Platform.OS === "web" && typeof window !== "undefined" ? window.location.origin + "/settings/account" : Linking.createURL("settings/account");
  const { url } = await apiFetch<{ url: string }>("/api/auth/google/link", { method: "POST", body: { redirect } });
  if (Platform.OS === "web") {
    window.location.href = url;
    return { kind: "pending" };
  }
  const result = await WebBrowser.openAuthSessionAsync(url, redirect);
  if (result.type !== "success" || !(result as any).url) return { kind: "cancelled" };
  return parseLinkResult((result as any).url);
}
