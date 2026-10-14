import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";

import { API_BASE, ApiError, apiFetch, clearToken, getToken, saveToken, setMemToken, setUnauthorizedHandler } from "@/src/api";
import { queryClient } from "@/src/query-client";
import { unregisterThisDevice } from "@/src/notifications";
import { appleCredential, googleNativeCredential } from "@/src/socialAuth";
import { beginOAuthAttempt, endOAuthAttempt, verifierFor } from "@/src/oauthPending";
import { parseOAuthReturn } from "@/src/pkce";
import { useLanguage } from "@/src/i18n/LanguageProvider";

WebBrowser.maybeCompleteAuthSession();

export type User = {
  user_id: string;
  name: string;
  email: string;
  avatar_url?: string | null;
  bio: string;
  /** Area names and coordinates exist only once the device has reported a location (null before). */
  city: string | null;
  neighborhood: string | null;
  lat?: number | null;
  lng?: number | null;
  location_updated_at?: string | null;
  genres: string[];
  languages: string[];
  reading_interests: string[];
  badges: { id: string; label: string; threshold: number; blurb: string; earned: boolean }[];
  is_exchanging: boolean;
  rating: number;
  rating_count: number;
  swaps_count: number;
  preferred_language?: string | null;
  /** False only right after a brand-new registration, until the one-time Welcome screen is dismissed. */
  onboarding_completed: boolean;
  /** Push notifications opt-in (Profile -> Notifications). Absent from older servers = off. */
  notifications_enabled?: boolean;
};

/** Response shape shared by request-otp and resend-otp. `debug_otp` only exists when the backend's
 * OTP_DEBUG_MODE is on (local development/testing) — never rely on it being present. */
export type OtpRequestResult = { ok: boolean; expires_in_seconds: number; resend_after_seconds: number; debug_otp?: string };

type Status = "loading" | "authed" | "guest";

type AuthContextType = {
  user: User | null;
  status: Status;
  /** Step 1 of registration: name + email -> sends a 6-digit code. */
  requestOtp: (name: string, email: string, preferredLanguage?: string) => Promise<OtpRequestResult>;
  /** Requests a fresh code for an in-progress registration; invalidates the previous one. */
  resendOtp: (email: string) => Promise<OtpRequestResult>;
  /** Step 2: redeems the code for a short-lived token proving email ownership. */
  verifyOtp: (email: string, code: string) => Promise<{ verification_token: string; expires_in_seconds: number }>;
  /** Step 3: creates the account (server independently re-validates the password) and signs in. */
  completeRegistration: (email: string, password: string, verificationToken: string) => Promise<void>;
  /** Marks the one-time Welcome screen as seen. Never shown again after this resolves. */
  completeOnboarding: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  loginWithGoogle: () => Promise<void>;
  /** Native Google sign-in (Android). Resolves false when the user cancelled Google's sheet. */
  loginWithGoogleNative: () => Promise<boolean>;
  /** Native Sign in with Apple (iOS). Resolves false when the user cancelled the Apple sheet. */
  loginWithApple: () => Promise<boolean>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  setUser: (u: User) => void;
  /** Persist the chosen UI language to the signed-in user's profile. */
  setPreferredLanguage: (code: string) => Promise<void>;
  /** True when a Google sign-in redirect came back but the session exchange failed. */
  googleError: boolean;
  clearGoogleError: () => void;
};

const AuthContext = createContext<AuthContextType>({} as AuthContextType);

/** What a URL meant for Google sign-in: signed in, a sign-in of ours that failed, or not ours at all. */
type GoogleReturn = "ok" | "failed" | "ignored";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const { language } = useLanguage();
  const [googleError, setGoogleError] = useState(false);
  // One in-flight exchange per session_id: a cold-start URL and a hot deep-link event can
  // deliver the same id, and both callers must see the same outcome.
  const inflight = useRef<Map<string, Promise<boolean>>>(new Map());

  const applySession = useCallback(async (token: string, u: User) => {
    await saveToken(token);
    // Never show the previous account's cached data to the next user.
    queryClient.clear();
    setUser(u);
    setStatus("authed");
  }, []);

  /** Exchanges a Google session_id (plus this attempt's verifier) for an app session. Resolves true on
   * success, false on failure. */
  const processSessionId = useCallback(
    (sid: string, verifier: string): Promise<boolean> => {
      if (!sid) return Promise.resolve(false);
      const existing = inflight.current.get(sid);
      if (existing) return existing;
      const p = (async () => {
        try {
          const data = await apiFetch<{ session_token: string; user: User }>("/api/auth/session", {
            method: "POST",
            body: { session_id: sid, code_verifier: verifier },
          });
          await applySession(data.session_token, data.user);
          return true;
        } catch {
          return false;
        }
      })();
      inflight.current.set(sid, p);
      return p;
    },
    [applySession],
  );

  /**
   * Any URL that may be the backend's Google sign-in redirect (browser result, launch link, deep link).
   * It is only acted on when it carries the `attempt` of the sign-in this app has pending; anything
   * else (an old attempt, a link someone crafted or shared) is ignored without a word.
   */
  const handleGoogleReturn = useCallback(
    async (url: string): Promise<GoogleReturn> => {
      const r = parseOAuthReturn(url);
      if (!r.sessionId && r.googleError === null) return "ignored";
      const verifier = await verifierFor("login", r.attempt);
      if (!verifier) return "ignored";
      if (!r.sessionId) {
        await endOAuthAttempt("login", r.attempt);
        return "failed";
      }
      const ok = await processSessionId(r.sessionId, verifier);
      // The ticket is single-use either way: this attempt is over.
      await endOAuthAttempt("login", r.attempt);
      return ok ? "ok" : "failed";
    },
    [processSessionId],
  );

  const refreshUser = useCallback(async () => {
    try {
      const data = await apiFetch<{ user: User }>("/api/auth/me");
      setUser(data.user);
    } catch {
      // ignore
    }
  }, []);

  // Bootstrap
  useEffect(() => {
    let mounted = true;
    (async () => {
      // A Google sign-in redirect arrives in the URL (web) or the launch link (native, also when Android
      // restarted the app while the browser was open — the pending verifier is in SecureStore).
      const web = Platform.OS === "web" && typeof window !== "undefined";
      const launchUrl = web ? window.location.hash || window.location.search : await Linking.getInitialURL();
      if (launchUrl) {
        const r = parseOAuthReturn(launchUrl);
        const outcome = await handleGoogleReturn(launchUrl);
        if (web && (r.sessionId || r.googleError !== null)) {
          try {
            window.history.replaceState(window.history.state, "", window.location.pathname);
          } catch {}
        }
        if (outcome === "ok") return; // applySession already set the user and the "authed" status
        // Sign-in failed: don't hang on the splash screen. Tell the login screen and fall through
        // to an existing stored session (if any) or the guest state.
        if (outcome === "failed" && mounted) setGoogleError(true);
      }

      // Existing stored session
      const token = await getToken();
      if (token) {
        setMemToken(token);
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            const data = await apiFetch<{ user: User }>("/api/auth/me");
            if (mounted) {
              setUser(data.user);
              setStatus("authed");
            }
            return;
          } catch (e) {
            if (e instanceof ApiError && e.status === 401) {
              // The server rejected the token: it is expired or revoked, so drop it.
              await clearToken();
              break;
            }
            // Network / server hiccup: keep the token so the next launch can retry.
            if (attempt < 2) await new Promise((r) => setTimeout(r, 800 * (attempt + 1)));
          }
        }
      }
      if (mounted) setStatus("guest");
    })();
    return () => {
      mounted = false;
    };
  }, [handleGoogleReturn]);

  // Native hot deep links
  useEffect(() => {
    if (Platform.OS === "web") return;
    const sub = Linking.addEventListener("url", ({ url }) => {
      handleGoogleReturn(url).then((outcome) => {
        if (outcome === "failed") setGoogleError(true);
      });
    });
    return () => sub.remove();
  }, [handleGoogleReturn]);

  // Registration: request-otp -> verify-otp -> complete. None of this trusts the client — the
  // backend independently enforces OTP expiry/attempts and the password policy either way.
  const requestOtp = useCallback(
    (name: string, email: string, preferredLanguage?: string) =>
      apiFetch<OtpRequestResult>("/api/auth/register/request-otp", {
        method: "POST",
        // The language picked on the first-run screen is saved with the new account, same as before.
        body: { name, email, preferred_language: preferredLanguage ?? language },
      }),
    [language],
  );

  const resendOtp = useCallback(
    (email: string) => apiFetch<OtpRequestResult>("/api/auth/register/resend-otp", { method: "POST", body: { email } }),
    [],
  );

  const verifyOtp = useCallback(
    (email: string, code: string) =>
      apiFetch<{ verification_token: string; expires_in_seconds: number }>("/api/auth/register/verify-otp", {
        method: "POST",
        body: { email, code },
      }),
    [],
  );

  const completeRegistration = useCallback(
    async (email: string, password: string, verificationToken: string) => {
      const data = await apiFetch<{ session_token: string; user: User }>("/api/auth/register/complete", {
        method: "POST",
        body: { email, password, verification_token: verificationToken },
      });
      await applySession(data.session_token, data.user);
    },
    [applySession],
  );

  const completeOnboarding = useCallback(async () => {
    const data = await apiFetch<{ user: User }>("/api/users/me", { method: "PUT", body: { onboarding_completed: true } });
    setUser(data.user);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const data = await apiFetch<{ session_token: string; user: User }>("/api/auth/login", {
        method: "POST",
        body: { email, password },
      });
      await applySession(data.session_token, data.user);
    },
    [applySession],
  );

  const loginWithGoogle = useCallback(async () => {
    const redirectUrl =
      Platform.OS === "web" && typeof window !== "undefined"
        ? window.location.origin + "/"
        : Linking.createURL("");
    // A new attempt: its verifier stays on this device, the backend only sees its SHA-256.
    const { challenge, attempt } = await beginOAuthAttempt("login");
    // Our own backend runs the Google flow (see /api/auth/google/start in backend/server.py).
    const authUrl =
      `${API_BASE}/api/auth/google/start?redirect=${encodeURIComponent(redirectUrl)}` +
      `&code_challenge=${encodeURIComponent(challenge)}&code_challenge_method=S256`;
    if (Platform.OS === "web") {
      window.location.href = authUrl;
      return;
    }
    // Register listener before opening; only this attempt's return counts.
    let captured: string | null = null;
    const sub = Linking.addEventListener("url", ({ url }) => {
      if (!captured && parseOAuthReturn(url).attempt === attempt) captured = url;
    });
    try {
      const result = await WebBrowser.openAuthSessionAsync(authUrl, redirectUrl);
      let url: string | null = null;
      if (result.type === "success" && (result as any).url) url = (result as any).url;
      if (!url && captured) url = captured;
      if (!url) {
        const initial = await Linking.getInitialURL();
        if (initial && parseOAuthReturn(initial).attempt === attempt) url = initial;
      }
      // No return of this attempt means the user closed the browser sheet: nothing to report, and the
      // attempt is over. A rejected sign-in, or a session_id that fails to exchange, is reported
      // through googleError (shared with the deep-link listener).
      const outcome = url ? await handleGoogleReturn(url) : "ignored";
      if (outcome === "failed") setGoogleError(true);
      else if (outcome === "ignored") await endOAuthAttempt("login", attempt);
    } finally {
      sub.remove();
    }
  }, [handleGoogleReturn]);

  const loginWithGoogleNative = useCallback(async () => {
    const cred = await googleNativeCredential();
    if (!cred) return false;
    // The backend verifies Google's signature, our Web client as audience, our Android app as the
    // authorized party, expiry, a verified email and its own single-use nonce before anyone is signed in.
    const data = await apiFetch<{ session_token: string; user: User }>("/api/auth/google/native", { method: "POST", body: cred });
    await applySession(data.session_token, data.user);
    return true;
  }, [applySession]);

  const loginWithApple = useCallback(async () => {
    const cred = await appleCredential();
    if (!cred) return false;
    const data = await apiFetch<{ session_token: string; user: User }>("/api/auth/apple", { method: "POST", body: cred });
    await applySession(data.session_token, data.user);
    return true;
  }, [applySession]);

  const logout = useCallback(async () => {
    // This device must stop receiving the account's pushes; done while the session still works.
    await unregisterThisDevice();
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch {}
    await clearToken();
    // A Link Google started by this account must not be finished by whoever signs in next.
    await endOAuthAttempt("link");
    queryClient.clear();
    setUser(null);
    setStatus("guest");
  }, []);

  // A 401 on a request that carried a token means the session expired or was revoked
  // (sessions last 7 days): sign out locally so the user lands on the login screen.
  useEffect(() => {
    setUnauthorizedHandler(async (usedToken) => {
      // Ignore a late 401 from an old session if a newer login has replaced the token.
      if ((await getToken()) !== usedToken) return;
      await clearToken();
      queryClient.clear();
      setUser(null);
      setStatus((s) => (s === "loading" ? s : "guest"));
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const clearGoogleError = useCallback(() => setGoogleError(false), []);

  const setPreferredLanguage = useCallback(async (code: string) => {
    // Optimistically reflect locally; the server is the source of truth on next refresh.
    setUser((prev) => (prev ? { ...prev, preferred_language: code } : prev));
    try {
      await apiFetch("/api/users/me", { method: "PUT", body: { preferred_language: code } });
    } catch {
      // Non-fatal: local persistence already happened in LanguageProvider.
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        status,
        requestOtp,
        resendOtp,
        verifyOtp,
        completeRegistration,
        completeOnboarding,
        login,
        loginWithGoogle,
        loginWithGoogleNative,
        loginWithApple,
        logout,
        refreshUser,
        setUser,
        setPreferredLanguage,
        googleError,
        clearGoogleError,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
