import { useEffect, useRef } from "react";
import { ActivityIndicator, View } from "react-native";
import { Stack, useRouter, useSegments } from "expo-router";
import { LogBox } from "react-native";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
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
import { ConstructionOverlay } from "@/src/components/ConstructionOverlay";
import { fontAssets } from "@/src/typography";
import { useTheme } from "@/src/theme";
import { AppearanceProvider } from "@/src/appearance";
import { LanguageProvider, useLanguage } from "@/src/i18n/LanguageProvider";

LogBox.ignoreAllLogs(true);
SplashScreen.preventAutoHideAsync().catch(() => {});

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
  const { scheme } = useTheme();
  return <StatusBar style={scheme === "dark" ? "light" : "dark"} />;
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

  if (status === "loading" || !isReady) {
    // The app's normal background, not an unexplained blank frame — and bounded: `isReady` always resolves
    // (see LanguageProvider's boot-reload fail-safe), so this never lingers as a stuck loading state.
    return (
      <View style={{ flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color={colors.brandPrimary} />
      </View>
    );
  }

  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.surface } }}>
      <Stack.Screen name="index" />
      <Stack.Screen name="(auth)" />
      <Stack.Screen name="welcome" />
      <Stack.Screen name="settings/language" options={{ presentation: "modal" }} />
      <Stack.Screen name="settings/appearance" options={{ presentation: "modal" }} />
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
            <AppearanceProvider>
              <ThemedStatusBar />
              <QueryClientProvider client={queryClient}>
                <LanguageProvider>
                  <DirectionRoot>
                    {/* Purely a visual overlay, mounted independently of auth/routing so it can never
                        gate or delay either — both proceed underneath it exactly as if it weren't
                        there. See src/components/ConstructionOverlay.tsx for the cold-launch-only logic. */}
                    <ConstructionOverlay />
                    <AuthProvider>
                      <BottomSheetModalProvider>
                        <ToastProvider>
                          <LanguageSync />
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
