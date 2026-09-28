import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";

import { API_BASE, ApiError, apiFetch, clearToken, getToken, saveToken, setMemToken, setUnauthorizedHandler } from "@/src/api";
import { queryClient } from "@/src/query-client";
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

function extractSessionId(url: string): string | null {
  const m = url.match(/[?#&]session_id=([^&#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

// Google sign-in that Google or the backend turned down comes back as `?google_error=...`.
function hasGoogleError(url: string): boolean {
  return /[?#&]google_error=/.test(url);
}

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

  /** Exchanges a Google session_id for an app session. Resolves true on success, false on failure. */
  const processSessionId = useCallback(
    (sid: string): Promise<boolean> => {
      if (!sid) return Promise.resolve(false);
      const existing = inflight.current.get(sid);
      if (existing) return existing;
      const p = (async () => {
        try {
          const data = await apiFetch<{ session_token: string; user: User }>("/api/auth/session", {
            method: "POST",
            body: { session_id: sid },
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
      // A Google sign-in redirect carries a session_id in the URL (web) or the launch link (native).
      let sid: string | null = null;
      let googleRejected = false;
      if (Platform.OS === "web" && typeof window !== "undefined") {
        const raw = window.location.hash || window.location.search;
        sid = raw ? extractSessionId(raw) : null;
        googleRejected = !!raw && hasGoogleError(raw);
        if (googleRejected) {
          try {
            window.history.replaceState(window.history.state, "", window.location.pathname);
          } catch {}
        }
      } else {
        const initial = await Linking.getInitialURL();
        sid = initial ? extractSessionId(initial) : null;
        googleRejected = !!initial && hasGoogleError(initial);
      }
      if (googleRejected && mounted) setGoogleError(true);
      if (sid) {
        const ok = await processSessionId(sid);
        if (Platform.OS === "web" && typeof window !== "undefined") {
          try {
            window.history.replaceState(window.history.state, "", window.location.pathname);
          } catch {}
        }
        if (ok) return; // applySession already set the user and the "authed" status
        // Exchange failed: don't hang on the splash screen. Tell the login screen and fall through
        // to an existing stored session (if any) or the guest state.
        if (mounted) setGoogleError(true);
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
  }, [processSessionId]);

  // Native hot deep links
  useEffect(() => {
    if (Platform.OS === "web") return;
    const sub = Linking.addEventListener("url", ({ url }) => {
      const sid = extractSessionId(url);
      if (sid) {
        processSessionId(sid).then((ok) => {
          if (!ok) setGoogleError(true);
        });
      } else if (hasGoogleError(url)) {
        setGoogleError(true);
      }
    });
    return () => sub.remove();
  }, [processSessionId]);

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
    // Our own backend runs the Google flow (see /api/auth/google/start in backend/server.py).
    const authUrl = `${API_BASE}/api/auth/google/start?redirect=${encodeURIComponent(redirectUrl)}`;
    if (Platform.OS === "web") {
      window.location.href = authUrl;
      return;
    }
    // Register listener before opening
    let captured: string | null = null;
    const sub = Linking.addEventListener("url", ({ url }) => {
      if (!captured) captured = url;
    });
    try {
      const result = await WebBrowser.openAuthSessionAsync(authUrl, redirectUrl);
      let url: string | null = null;
      if (result.type === "success" && (result as any).url) url = (result as any).url;
      if (!url && captured) url = captured;
      if (!url) url = await Linking.getInitialURL();
      const sid = url ? extractSessionId(url) : null;
      // No sid and no error means the user closed the browser sheet: nothing to report. A rejected
      // sign-in, or a sid that fails to exchange, is reported through googleError (shared with the
      // deep-link listener).
      if (url && hasGoogleError(url)) setGoogleError(true);
      else if (sid && !(await processSessionId(sid))) setGoogleError(true);
    } finally {
      sub.remove();
    }
  }, [processSessionId]);

  const logout = useCallback(async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch {}
    await clearToken();
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
