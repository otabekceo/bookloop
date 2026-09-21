import { useEffect, useState } from "react";
import { View, Pressable, ImageBackground } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GoogleLogo } from "phosphor-react-native";

import { AppText, Button, Field, useToast, haptic } from "@/src/components/ui";
import { Logo } from "@/src/components/Logo";
import { useAuth } from "@/src/auth";
import { apiFetch } from "@/src/api";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";
import { fontsForLanguage } from "@/src/typography";

const AUTH_BG =
  "https://images.unsplash.com/photo-1518373714866-3f1478910cc0?crop=entropy&cs=srgb&fm=jpg&w=1200&q=80";

export default function Login() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { login, register, loginWithGoogle, googleError, clearGoogleError } = useAuth();
  const { t, language: uiLanguage } = useLanguage();
  const uiFonts = fontsForLanguage(uiLanguage);
  const toast = useToast();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  // Show the Google button only when the backend has a Google OAuth client configured.
  const [googleEnabled, setGoogleEnabled] = useState(false);
  useEffect(() => {
    let live = true;
    apiFetch<{ google: boolean }>("/api/auth/providers")
      .then((p) => live && setGoogleEnabled(!!p.google))
      .catch(() => live && setGoogleEnabled(false));
    return () => {
      live = false;
    };
  }, []);

  // A Google redirect that came back but couldn't be exchanged for a session.
  useEffect(() => {
    if (!googleError) return;
    toast(t("auth.googleFailed"), "error");
    clearGoogleError();
  }, [googleError, clearGoogleError, toast, t]);

  const submit = async () => {
    if (!email.trim() || !password.trim() || (mode === "register" && !name.trim())) {
      toast(t("auth.fillAllFields"), "error");
      return;
    }
    setLoading(true);
    try {
      if (mode === "login") await login(email.trim(), password);
      else await register(email.trim(), password, name.trim());
      haptic("success");
    } catch (e: any) {
      toast(e.message || t("auth.somethingWentWrong"), "error");
    } finally {
      setLoading(false);
    }
  };

  const google = async () => {
    try {
      await loginWithGoogle();
    } catch {
      toast(t("auth.googleFailed"), "error");
    }
  };

  return (
    <View style={styles.root}>
      <ImageBackground source={{ uri: AUTH_BG }} style={styles.bg} resizeMode="cover">
        <LinearGradient
          colors={["rgba(23,33,31,0.15)", "rgba(23,33,31,0.55)", colors.surface]}
          locations={[0, 0.45, 0.92]}
          style={styles.scrim}
        />
      </ImageBackground>

      <KeyboardAwareScrollView
        style={styles.scroll}
        contentContainerStyle={[styles.content, { paddingTop: insets.top + 80, paddingBottom: insets.bottom + 24 }]}
        bottomOffset={24}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.brandWrap}>
          <AppText color="#FFFFFF" style={[styles.tagOverImage, { fontFamily: uiFonts.medium }]}>
            {t("common.tagline")}
          </AppText>
        </View>

        <View style={styles.card}>
          <View style={{ alignItems: "center", marginBottom: 18 }}>
            <Logo variant="full" height={64} />
          </View>
          <AppText variant="title" style={{ marginBottom: 4 }}>
            {mode === "login" ? t("auth.welcomeBack") : t("auth.joinLoop")}
          </AppText>
          <AppText variant="body" color={colors.muted} style={{ marginBottom: 18 }}>
            {mode === "login" ? t("auth.signInSubtitle") : t("auth.registerSubtitle")}
          </AppText>

          <View style={{ gap: 12 }}>
            {mode === "register" && (
              <Field
                testID="name-input"
                label={t("auth.name")}
                placeholder={t("auth.namePlaceholder")}
                value={name}
                onChangeText={setName}
                onSurface
              />
            )}
            <Field
              testID="email-input"
              label={t("auth.email")}
              placeholder={t("auth.emailPlaceholder")}
              autoCapitalize="none"
              keyboardType="email-address"
              value={email}
              onChangeText={setEmail}
              onSurface
            />
            <Field
              testID="password-input"
              label={t("auth.password")}
              placeholder="••••••••"
              secureTextEntry
              value={password}
              onChangeText={setPassword}
              onSurface
            />
          </View>

          <Button
            testID="auth-submit-button"
            title={mode === "login" ? t("auth.signIn") : t("auth.createAccount")}
            onPress={submit}
            loading={loading}
            style={{ marginTop: 18 }}
          />

          {googleEnabled && (
            <>
              <View style={styles.divider}>
                <View style={styles.line} />
                <AppText variant="caption" color={colors.muted}>
                  {t("common.or")}
                </AppText>
                <View style={styles.line} />
              </View>

              <Button
                testID="google-button"
                title={t("auth.continueWithGoogle")}
                variant="outline"
                onPress={google}
                icon={<GoogleLogo size={20} color={colors.brandPrimary} weight="bold" />}
              />
            </>
          )}

          <Pressable
            testID="toggle-mode-button"
            onPress={() => setMode(mode === "login" ? "register" : "login")}
            style={{ marginTop: 18, alignSelf: "center" }}
          >
            <AppText variant="label" color={colors.muted}>
              {mode === "login" ? t("auth.newHere") : t("auth.alreadyHaveAccount")}
              <AppText variant="label" color={colors.brandPrimary}>
                {mode === "login" ? t("auth.createAccount") : t("auth.signIn")}
              </AppText>
            </AppText>
          </Pressable>
        </View>
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  bg: { position: "absolute", top: 0, left: 0, right: 0, height: 420 },
  scrim: { flex: 1 },
  scroll: { flex: 1 },
  content: { paddingHorizontal: 20 },
  brandWrap: { alignItems: "center", marginBottom: 20 },
  tagOverImage: { fontSize: 15, letterSpacing: 1, textShadowColor: "rgba(0,0,0,0.3)", textShadowRadius: 6 },
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: 24,
    padding: 22,
    borderWidth: 1,
    borderColor: colors.border,
  },
  divider: { flexDirection: "row", alignItems: "center", gap: 12, marginVertical: 16 },
  line: { flex: 1, height: 1, backgroundColor: colors.border },
}));
