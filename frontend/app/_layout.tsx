import { useEffect, useRef } from "react";
import { View } from "react-native";
import { Stack, useRouter, useSegments } from "expo-router";
import { LogBox } from "react-native";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { KeyboardProvider } from "react-native-keyboard-controller";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { BottomSheetModalProvider } from "@gorhom/bottom-sheet";
import { QueryClientProvider } from "@tanstack/react-query";

import { ErrorBoundary } from "@/src/components/error-boundary";
import { queryClient } from "@/src/query-client";
import { AuthProvider, useAuth } from "@/src/auth";
import { ToastProvider } from "@/src/components/ui";
import { fontAssets } from "@/src/typography";
import { useTheme } from "@/src/theme";
import { LanguageProvider, useLanguage } from "@/src/i18n/LanguageProvider";

LogBox.ignoreAllLogs(true);
SplashScreen.preventAutoHideAsync().catch(() => {});

/**
 * Adopts the signed-in user's server-side language preference when no explicit
 * local choice exists yet (e.g. signing in on a new device). Renders nothing.
 */
function LanguageSync() {
  const { user } = useAuth();
  const { syncFromServer } = useLanguage();
  const syncedRef = useRef(false);

  useEffect(() => {
    if (syncedRef.current) return;
    if (!user?.preferred_language) return;
    syncedRef.current = true;
    syncFromServer(user.preferred_language);
  }, [user?.preferred_language, syncFromServer]);

  return null;
}

function RootNavigator() {
  const { status } = useAuth();
  const { isReady, needsSelection } = useLanguage();
  const segments = useSegments();
  const router = useRouter();
  const { colors } = useTheme();

  useEffect(() => {
    if (status === "loading" || !isReady) return;

    const segs = segments as string[];
    const inAuth = segs[0] === "(auth)";
    const onLanguageScreen = inAuth && segs[1] === "language";

    // Brand-new users must pick a language before they can sign in or sign up.
    if (needsSelection) {
      if (!onLanguageScreen) router.replace("/(auth)/language");
      return;
    }

    if (status === "guest" && !inAuth) {
      router.replace("/(auth)/login");
    } else if (status === "authed" && (inAuth || segments.length === 0)) {
      router.replace("/(tabs)/discover");
    }
  }, [status, isReady, needsSelection, segments, router]);

  if (status === "loading" || !isReady) {
    return <View style={{ flex: 1, backgroundColor: colors.surface }} />;
  }

  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.surface } }}>
      <Stack.Screen name="index" />
      <Stack.Screen name="(auth)" />
      <Stack.Screen name="settings/language" options={{ presentation: "modal" }} />
      <Stack.Screen name="(tabs)" />
      <Stack.Screen name="person/[id]" options={{ presentation: "card" }} />
      <Stack.Screen name="book/[id]" options={{ presentation: "card" }} />
      <Stack.Screen name="swap/[id]" options={{ presentation: "card" }} />
      <Stack.Screen name="reviews/[id]" options={{ presentation: "card" }} />
      <Stack.Screen name="book/add" options={{ presentation: "modal" }} />
      <Stack.Screen name="book/scan" options={{ presentation: "fullScreenModal" }} />
      <Stack.Screen name="edit-profile" options={{ presentation: "modal" }} />
      <Stack.Screen name="wishlist" options={{ presentation: "card" }} />
    </Stack>
  );
}

export default function RootLayout() {
  const [fontsLoaded] = useFonts(fontAssets);

  useEffect(() => {
    if (fontsLoaded) SplashScreen.hideAsync().catch(() => {});
  }, [fontsLoaded]);

  if (!fontsLoaded) return null;

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <KeyboardProvider>
        <SafeAreaProvider>
          <ErrorBoundary>
            <QueryClientProvider client={queryClient}>
              <LanguageProvider>
                <AuthProvider>
                  <BottomSheetModalProvider>
                    <ToastProvider>
                      <LanguageSync />
                      <RootNavigator />
                    </ToastProvider>
                  </BottomSheetModalProvider>
                </AuthProvider>
              </LanguageProvider>
            </QueryClientProvider>
          </ErrorBoundary>
        </SafeAreaProvider>
      </KeyboardProvider>
    </GestureHandlerRootView>
  );
}
