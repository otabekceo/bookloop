import { Platform } from "react-native";

import { storage } from "@/src/utils/storage";

const BASE = ((process.env.EXPO_PUBLIC_BACKEND_URL as string | undefined) || "").replace(/\/+$/, "");
export const API_BASE = BASE;

const MISSING_BASE_MESSAGE =
  "The BookLoop backend address is not set. Add EXPO_PUBLIC_BACKEND_URL to frontend/.env and restart Metro with `npx expo start -c`.";
export const TOKEN_KEY = "bookloop_session_token";

let memToken: string | null = null;

export function setMemToken(t: string | null) {
  memToken = t;
}

export async function getToken(): Promise<string | null> {
  if (memToken) return memToken;
  const t = await storage.secureGet<string>(TOKEN_KEY, null as any);
  memToken = t;
  return t;
}

export async function saveToken(t: string) {
  memToken = t;
  await storage.secureSet(TOKEN_KEY, t);
}

export async function clearToken() {
  memToken = null;
  await storage.secureRemove(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

type Options = {
  method?: string;
  body?: any;
  headers?: Record<string, string>;
};

// Called when a request that carried a session token comes back 401 (expired or revoked
// session), so the app can drop back to the login screen instead of failing every call.
let onUnauthorized: ((usedToken: string) => void) | null = null;

export function setUnauthorizedHandler(fn: ((usedToken: string) => void) | null) {
  onUnauthorized = fn;
}

// Credential-exchange endpoints never need (or should send) an existing session token.
const NO_TOKEN_PATHS = ["/api/auth/login", "/api/auth/register", "/api/auth/session"];

export async function apiFetch<T = any>(path: string, opts: Options = {}): Promise<T> {
  if (!BASE) throw new ApiError(MISSING_BASE_MESSAGE, 0);
  const sendToken = !NO_TOKEN_PATHS.some((p) => path.startsWith(p));
  const token = sendToken ? await getToken() : null;
  const headers: Record<string, string> = { ...(opts.headers || {}) };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(BASE + path, {
    method: opts.method || "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && token) onUnauthorized?.(token);
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      msg = data.detail || msg;
    } catch {
      // ignore
    }
    throw new ApiError(msg, res.status);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}

// Resolve an image reference (absolute URL or backend /api/files path) to a
// displayable URL.
export function resolveImage(url?: string | null): string | undefined {
  if (!url) return undefined;
  if (url.startsWith("http")) return url;
  return BASE + url;
}

// Upload an image picked from the device and return its backend path + url.
export async function uploadImage(uri: string): Promise<{ path: string; url: string }> {
  const token = await getToken();
  const name = `photo_${Date.now()}.jpg`;
  const form = new FormData();
  if (Platform.OS === "web") {
    const blob = await (await fetch(uri)).blob();
    form.append("file", blob, name);
  } else {
    form.append("file", { uri, name, type: "image/jpeg" } as any);
  }
  const res = await fetch(BASE + "/api/upload", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });
  if (!res.ok) throw new ApiError("Upload failed", res.status);
  return res.json();
}
