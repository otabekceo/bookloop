import { useEffect, useRef } from "react";
import { useRouter, useSegments } from "expo-router";
import { useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/src/auth";
import {
  Notifications,
  PUSH_SUPPORTED,
  checkPermission,
  configureNotificationHandler,
  refreshRegistrationSilently,
  routeFromNotificationData,
} from "@/src/notifications";

/**
 * Renders nothing. For a signed-in user it: keeps an opted-in device's push token fresh (never
 * prompting), refreshes the badge counters when a push arrives in the foreground, and opens the
 * right conversation when a push is tapped — including the one that launched the app, which waits
 * until the app has left its startup routes so the initial redirect can't swallow it.
 */
export function NotificationsBootstrap() {
  const { status, user } = useAuth();
  const router = useRouter();
  const segments = useSegments() as string[];
  const qc = useQueryClient();
  const pendingRoute = useRef<string | null>(null);

  useEffect(() => {
    configureNotificationHandler();
  }, []);

  useEffect(() => {
    if (status !== "authed") return;
    if (user?.notifications_enabled) refreshRegistrationSilently();
    else checkPermission();
  }, [status, user?.user_id, user?.notifications_enabled]);

  const settled = status === "authed" && segments.length > 0 && segments[0] !== "(auth)" && segments[0] !== "welcome";

  useEffect(() => {
    if (!PUSH_SUPPORTED || status !== "authed") return;
    const open = (data: unknown) => {
      const route = routeFromNotificationData(data);
      if (!route) return;
      pendingRoute.current = route;
      qc.invalidateQueries({ queryKey: ["notifications"] });
    };
    Notifications.getLastNotificationResponseAsync()
      .then((r) => {
        if (!r) return;
        open(r.notification.request.content.data);
        Notifications.clearLastNotificationResponseAsync().catch(() => {});
      })
      .catch(() => {});
    const tapped = Notifications.addNotificationResponseReceivedListener((r) => open(r.notification.request.content.data));
    const received = Notifications.addNotificationReceivedListener(() => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["notificationFeed"] });
    });
    return () => {
      tapped.remove();
      received.remove();
    };
  }, [status, qc]);

  // Navigate once the app is on a real screen (after the startup redirect).
  useEffect(() => {
    if (!settled || !pendingRoute.current) return;
    const route = pendingRoute.current;
    pendingRoute.current = null;
    router.push(route as any);
  });

  return null;
}
