/**
 * Push notifications (expo-notifications). Nothing here runs at launch on its own: the OS permission
 * is only requested when the user turns Notifications ON in Profile (enableNotifications). At startup
 * the app only re-registers a device whose permission was ALREADY granted (tokens can rotate).
 *
 * Real delivery needs an Expo push token, which needs push credentials configured for the build
 * (FCM for Android, APNs for iOS — see the v2.2 notes). Without them getExpoPushTokenAsync fails; that
 * is reported as `pushReady: false` ("in-app only") instead of pretending push works.
 */
import { useSyncExternalStore } from "react";
import { Platform } from "react-native";
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";

import { apiFetch } from "@/src/api";
import { storage } from "@/src/utils/storage";

export type Permission = "granted" | "denied" | "undetermined" | "unsupported";

const SUPPORTED = Platform.OS === "android" || Platform.OS === "ios";
const TOKEN_KEY = "bookloop_push_token";
export const ANDROID_CHANNEL_ID = "default";

// --- A tiny store so Profile re-renders when the push state changes ------------------------------
type PushStatus = { permission: Permission; canAskAgain: boolean; pushReady: boolean; checked: boolean };
let status: PushStatus = { permission: SUPPORTED ? "undetermined" : "unsupported", canAskAgain: true, pushReady: false, checked: !SUPPORTED };
const listeners = new Set<() => void>();
function setStatus(patch: Partial<PushStatus>) {
  status = { ...status, ...patch };
  listeners.forEach((l) => l());
}
export function usePushStatus(): PushStatus {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => status,
    () => status,
  );
}

// --- Foreground behaviour --------------------------------------------------------------------------
// The conversation currently on screen: a push about it is not shown on top of it.
let activeSwapId: string | null = null;
export function setActiveSwap(id: string | null) {
  activeSwapId = id;
}

let handlerConfigured = false;
export function configureNotificationHandler() {
  if (!SUPPORTED || handlerConfigured) return;
  handlerConfigured = true;
  Notifications.setNotificationHandler({
    handleNotification: async (n) => {
      const swapId = (n.request.content.data as any)?.swap_id;
      const show = !(swapId && swapId === activeSwapId);
      return { shouldShowBanner: show, shouldShowList: show, shouldPlaySound: show, shouldSetBadge: false };
    },
  });
}

async function ensureAndroidChannel() {
  // Android 13+ only shows the permission prompt once a channel exists.
  if (Platform.OS !== "android") return;
  await Notifications.setNotificationChannelAsync(ANDROID_CHANNEL_ID, {
    name: "BookLoop",
    importance: Notifications.AndroidImportance.HIGH,
  });
}

function toPermission(p: { status: string; granted?: boolean }): Permission {
  if (p.granted || p.status === "granted") return "granted";
  return p.status === "denied" ? "denied" : "undetermined";
}

/** Reads the OS permission without ever prompting. */
export async function checkPermission(): Promise<Permission> {
  if (!SUPPORTED) return "unsupported";
  try {
    const p = await Notifications.getPermissionsAsync();
    const permission = toPermission(p);
    setStatus({ permission, canAskAgain: p.canAskAgain !== false, checked: true });
    return permission;
  } catch {
    setStatus({ checked: true });
    return status.permission;
  }
}

async function obtainPushToken(): Promise<string | null> {
  if (!Device.isDevice) return null; // simulators/emulators can't receive remote pushes
  const projectId = (Constants.expoConfig?.extra as any)?.eas?.projectId ?? (Constants as any).easConfig?.projectId;
  if (!projectId) return null;
  try {
    return (await Notifications.getExpoPushTokenAsync({ projectId })).data;
  } catch (e) {
    // Typical causes: no FCM credentials / google-services.json in this Android build, no APNs
    // entitlement on iOS, or Expo Go on Android (push was removed from Expo Go in SDK 53).
    console.warn("[push] no push token for this build:", (e as Error)?.message);
    return null;
  }
}

/** Registers this device's push token with the backend. Returns whether push delivery is possible. */
export async function registerThisDevice(): Promise<boolean> {
  const token = await obtainPushToken();
  if (!token) {
    setStatus({ pushReady: false });
    return false;
  }
  try {
    await apiFetch("/api/push/register", { method: "POST", body: { token, platform: Platform.OS } });
    await storage.setItem(TOKEN_KEY, token);
    setStatus({ pushReady: true });
    return true;
  } catch {
    setStatus({ pushReady: false });
    return false;
  }
}

/** Removes this device's token from the signed-in account (turning Notifications off, signing out). */
export async function unregisterThisDevice(): Promise<void> {
  const token = await storage.getItem<string | null>(TOKEN_KEY, null);
  setStatus({ pushReady: false });
  if (!token) return;
  try {
    await apiFetch("/api/push/unregister", { method: "POST", body: { token } });
  } catch {}
  await storage.removeItem(TOKEN_KEY);
}

/**
 * Turning Notifications ON: asks the OS only if it still can; on success turns the account's setting
 * on and registers this device. "denied" means the user has to allow it in the system settings.
 */
export async function enableNotifications(): Promise<"on" | "onNoPush" | "denied" | "unsupported"> {
  if (!SUPPORTED) return "unsupported";
  await ensureAndroidChannel();
  let permission = await checkPermission();
  if (permission !== "granted" && status.canAskAgain) {
    const p = await Notifications.requestPermissionsAsync();
    permission = toPermission(p);
    setStatus({ permission, canAskAgain: p.canAskAgain !== false, checked: true });
  }
  if (permission !== "granted") return "denied";
  await apiFetch("/api/users/me", { method: "PUT", body: { notifications_enabled: true } });
  return (await registerThisDevice()) ? "on" : "onNoPush";
}

/** Turning Notifications OFF: the server stops pushing to this account; unread state is kept. */
export async function disableNotifications(): Promise<void> {
  await apiFetch("/api/users/me", { method: "PUT", body: { notifications_enabled: false } });
  await unregisterThisDevice();
}

/** At startup, for a user who opted in: refresh the token only if permission was already granted. */
export async function refreshRegistrationSilently(): Promise<void> {
  if (!SUPPORTED) return;
  if ((await checkPermission()) !== "granted") return;
  await ensureAndroidChannel();
  await registerThisDevice();
}

/** The in-app route a tapped push points to (only this app's own conversation routes). */
export function routeFromNotificationData(data: unknown): string | null {
  const url = (data as any)?.url;
  return typeof url === "string" && /^\/swap\/[A-Za-z0-9_]+$/.test(url) ? url : null;
}

export { Notifications, SUPPORTED as PUSH_SUPPORTED };
