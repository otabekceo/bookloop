import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";

import { apiFetch, clearToken, getToken, saveToken, setMemToken } from "@/src/api";

WebBrowser.maybeCompleteAuthSession();

export type User = {
  user_id: string;
  name: string;
  email: string;
  avatar_url?: string | null;
  bio: string;
  city: string;
  neighborhood: string;
  lat?: number;
  lng?: number;
  genres: string[];
  languages: string[];
  is_exchanging: boolean;
  rating: number;
  rating_count: number;
  swaps_count: number;
};

type Status = "loading" | "authed" | "guest";

type AuthContextType = {
  user: User | null;
  status: Status;
  register: (email: string, password: string, name: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  loginWithGoogle: () => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  setUser: (u: User) => void;
};

const AuthContext = createContext<AuthContextType>({} as AuthContextType);

const AUTH_HOST = "https://auth.emergentagent.com";

function extractSessionId(url: string): string | null {
  const m = url.match(/[?#&]session_id=([^&#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const processed = useRef<Set<string>>(new Set());

  const applySession = useCallback(async (token: string, u: User) => {
    await saveToken(token);
    setUser(u);
    setStatus("authed");
  }, []);

  const processSessionId = useCallback(
    async (sid: string) => {
      if (!sid || processed.current.has(sid)) return;
      processed.current.add(sid);
      try {
        const data = await apiFetch<{ session_token: string; user: User }>("/api/auth/session", {
          method: "POST",
          body: { session_id: sid },
        });
        await applySession(data.session_token, data.user);
      } catch (e) {
        // leave as guest; surfaced by caller if needed
      }
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
      // Web: process session_id in the URL first
      if (Platform.OS === "web" && typeof window !== "undefined") {
        const raw = window.location.hash || window.location.search;
        const sid = raw ? extractSessionId(raw) : null;
        if (sid) {
          await processSessionId(sid);
          try {
            window.history.replaceState(window.history.state, "", window.location.pathname);
          } catch {}
          return;
        }
      } else {
        // Native: cold-start deep link
        const initial = await Linking.getInitialURL();
        if (initial) {
          const sid = extractSessionId(initial);
          if (sid) {
            await processSessionId(sid);
            return;
          }
        }
      }
      // Existing token
      const token = await getToken();
      if (token) {
        setMemToken(token);
        try {
          const data = await apiFetch<{ user: User }>("/api/auth/me");
          if (mounted) {
            setUser(data.user);
            setStatus("authed");
          }
          return;
        } catch {
          await clearToken();
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
      if (sid) processSessionId(sid);
    });
    return () => sub.remove();
  }, [processSessionId]);

  const register = useCallback(
    async (email: string, password: string, name: string) => {
      const data = await apiFetch<{ session_token: string; user: User }>("/api/auth/register", {
        method: "POST",
        body: { email, password, name },
      });
      await applySession(data.session_token, data.user);
    },
    [applySession],
  );

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
    const authUrl = `${AUTH_HOST}/?redirect=${encodeURIComponent(redirectUrl)}`;
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
      if (sid) await processSessionId(sid);
    } finally {
      sub.remove();
    }
  }, [processSessionId]);

  const logout = useCallback(async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch {}
    await clearToken();
    setUser(null);
    setStatus("guest");
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, status, register, login, loginWithGoogle, logout, refreshUser, setUser }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
