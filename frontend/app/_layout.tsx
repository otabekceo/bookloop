import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Platform, View } from "react-native";
import { Stack, useRouter, useSegments } from "expo-router";
import { LogBox } from "react-native";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
import * as SystemUI from "expo-system-ui";
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { KeyboardProvider } from "react-native-keyboard-controller";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { BottomSheetModalProvider } from "@gorhom/bottom-sheet";
import { QueryClientProvider } from "@tanstack/react-query";

import { ErrorBoundary } from "@/src/components/error-boundary";
import { queryClient } from "@/src/query-client";
import { AuthProvider, useAuth } from "@/src/auth";
import { ToastProvider } from "@/src/components/ui";
import { LanguageSwitchOverlay } from "@/src/components/LanguageSwitchOverlay";
import { NotificationsBootstrap } from "@/src/components/NotificationsBootstrap";
import { Logo } from "@/src/components/Logo";
import { fontAssets } from "@/src/typography";
import { useTheme } from "@/src/theme";
import { AppearanceProvider } from "@/src/appearance";
import { LanguageProvider, useLanguage } from "@/src/i18n/LanguageProvider";

LogBox.ignoreAllLogs(true);
// ONE splash: the native one stays up until the app knows where to go (fonts, stored language and the
// session check), then fades out straight into the first real screen. See hideSplash below.
SplashScreen.preventAutoHideAsync().catch(() => {});
SplashScreen.setOptions({ fade: true, duration: 250 });
// Never keep the splash up longer than this, even if the session check is slow (bad network): after
// it, StartupScreen — the same logo on the same background — takes over, so there is no visual jump.
const SPLASH_MAX_MS = 4000;

let splashHidden = false;
function hideSplash() {
  if (splashHidden) return;
  splashHidden = true;
  SplashScreen.hideAsync().catch(() => {});
}

/**
 * Adopts the signed-in user's server-side language preference when no explicit
 * local choice exists yet (e.g. signing in on a new device). Renders nothing.
 */
function LanguageSync() {
  const { user, setPreferredLanguage } = useAuth();
  const { language, isReady, needsSelection, syncFromServer } = useLanguage();
  const syncedRef = useRef(false);
  const pushedForRef = useRef<string | null>(null);

  useEffect(() => {
    if (syncedRef.current) return;
    if (!user?.preferred_language) return;
    syncedRef.current = true;
    syncFromServer(user.preferred_language);
  }, [user?.preferred_language, syncFromServer]);

  // The language picker runs before sign-in/sign-up, so the choice is made while signed out.
  // Once a user is signed in, save that explicit local choice to their profile (once per account
  // per session); waiting for isReady avoids pushing the default before storage was read.
  useEffect(() => {
    if (!user) {
      pushedForRef.current = null;
      return;
    }
    if (!isReady || needsSelection) return;
    if (pushedForRef.current === user.user_id) return;
    pushedForRef.current = user.user_id;
    if (user.preferred_language !== language) setPreferredLanguage(language);
  }, [user, isReady, needsSelection, language, setPreferredLanguage]);

  return null;
}

/** Status bar icon/text color follows the resolved theme (light icons on dark, dark icons on light) —
 * independent of language/direction, per the appearance system living entirely apart from RTL. */
function ThemedStatusBar() {
  const { scheme, colors } = useTheme();
  // The native root view's color (what shows behind screens during transitions and under the
  // keyboard) follows the theme, so no white/black flash ever shows behind the app.
  useEffect(() => {
    SystemUI.setBackgroundColorAsync(colors.surface).catch(() => {});
  }, [colors.surface]);
  return <StatusBar style={scheme === "dark" ? "light" : "dark"} />;
}

/** Shown only if startup outlasts SPLASH_MAX_MS: mirrors the native splash (same centered logo on the
 * same background), with a small spinner so a slow network doesn't look frozen. */
function StartupScreen() {
  const { colors } = useTheme();
  return (
    <View testID="startup-screen" style={{ flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center", gap: 28 }}>
      {/* Same size as the native splash logo (app.json: 150 wide on Android, 220 on iOS; ratio 723:430). */}
      <Logo variant="full" height={Platform.OS === "android" ? 89 : 131} />
      <ActivityIndicator color={colors.brandPrimary} />
    </View>
  );
}

/**
 * Applies the layout direction of the CURRENT language to the whole app as an explicit `direction`, so the
 * layout follows the language even when the native direction has not caught up yet (e.g. right after a
 * switch, or in a build that cannot reload). Everything below, bottom sheets and toasts included,
 * inherits it. The bottom tab bar deliberately overrides it with `ltr` (see app/(tabs)/_layout.tsx).
 */
function DirectionRoot({ children }: { children: React.ReactNode }) {
  const { isRTL } = useLanguage();
  return <View style={{ flex: 1, direction: isRTL ? "rtl" : "ltr" }}>{children}</View>;
}

function RootNavigator() {
  const { status, user } = useAuth();
  const { isReady, needsSelection } = useLanguage();
  const segments = useSegments();
  const router = useRouter();
  const { colors } = useTheme();

  useEffect(() => {
    if (status === "loading" || !isReady) return;

    const segs = segments as string[];
    const inAuth = segs[0] === "(auth)";
    const onLanguageScreen = inAuth && segs[1] === "language";
    const onWelcomeScreen = segs[0] === "welcome";

    // Brand-new users must pick a language before they can sign in or sign up.
    if (needsSelection) {
      if (!onLanguageScreen) router.replace("/(auth)/language");
      return;
    }

    // A guest belongs on the login screen. This includes a guest still sitting on the language
    // picker: once a language is chosen (needsSelection is false) the picker is done and must
    // hand over to login, otherwise the Continue button appears to do nothing.
    if (status === "guest" && (!inAuth || onLanguageScreen)) {
      router.replace("/(auth)/login");
    } else if (status === "authed" && user && !user.onboarding_completed) {
      // A brand-new registration (or first-time Google sign-up) that hasn't seen the one-time
      // Welcome screen yet: send them there regardless of where they landed, and keep them there
      // until it's done. Welcome's own CTA is what navigates away once completeOnboarding() resolves
      // — this effect only ever pushes them IN, never fights that explicit exit.
      if (!onWelcomeScreen) router.replace("/welcome");
    } else if (status === "authed" && (inAuth || (segments as string[]).length === 0)) {
      router.replace("/(tabs)/discover");
    }
  }, [status, user, isReady, needsSelection, segments, router]);

  const booting = status === "loading" || !isReady;
  useEffect(() => {
    if (!booting) hideSplash();
  }, [booting]);

  if (booting) {
    // Bounded: `isReady` always resolves (LanguageProvider's boot-reload fail-safe) and the session check
    // ends in "authed" or "guest". Normally the native splash is still covering this.
    return <StartupScreen />;
  }

  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.surface } }}>
      <Stack.Screen name="index" />
      <Stack.Screen name="(auth)" />
      <Stack.Screen name="welcome" />
      <Stack.Screen name="settings/language" options={{ presentation: "modal" }} />
      <Stack.Screen name="settings/appearance" options={{ presentation: "modal" }} />
      <Stack.Screen name="settings/account" options={{ presentation: "modal" }} />
      <Stack.Screen name="(tabs)" />
      <Stack.Screen name="person/[id]" options={{ presentation: "card" }} />
      <Stack.Screen name="book/[id]" options={{ presentation: "card" }} />
      <Stack.Screen name="swap/[id]" options={{ presentation: "card" }} />
      <Stack.Screen name="swap/new" options={{ presentation: "card" }} />
      <Stack.Screen name="notifications" options={{ presentation: "card" }} />
      <Stack.Screen name="favorites" options={{ presentation: "card" }} />
      <Stack.Screen name="reviews/[id]" options={{ presentation: "card" }} />
      <Stack.Screen name="book/add" options={{ presentation: "modal" }} />
      <Stack.Screen name="book/scan" options={{ presentation: "fullScreenModal" }} />
      <Stack.Screen name="edit-profile" options={{ presentation: "modal" }} />
      <Stack.Screen name="wishlist" options={{ presentation: "card" }} />
    </Stack>
  );
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts(fontAssets);
  const [splashTimedOut, setSplashTimedOut] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSplashTimedOut(true);
      hideSplash();
    }, SPLASH_MAX_MS);
    return () => clearTimeout(timer);
  }, []);

  // A font that fails to load must not keep the app on the splash forever: fall back to system fonts.
  if (!fontsLoaded && !fontError && !splashTimedOut) return null;

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <KeyboardProvider>
        <SafeAreaProvider>
          <ErrorBoundary>
            <AppearanceProvider>
              <ThemedStatusBar />
              <QueryClientProvider client={queryClient}>
                <LanguageProvider>
                  <DirectionRoot>
                    <AuthProvider>
                      <BottomSheetModalProvider>
                        <ToastProvider>
                          <LanguageSync />
                          <NotificationsBootstrap />
                          <RootNavigator />
                          <LanguageSwitchOverlay />
                        </ToastProvider>
                      </BottomSheetModalProvider>
                    </AuthProvider>
                  </DirectionRoot>
                </LanguageProvider>
              </QueryClientProvider>
            </AppearanceProvider>
          </ErrorBoundary>
        </SafeAreaProvider>
      </KeyboardProvider>
    </GestureHandlerRootView>
  );
}
