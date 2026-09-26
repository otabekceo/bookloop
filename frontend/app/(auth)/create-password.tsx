import { useMemo, useState } from "react";
import { Pressable, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { Check, Eye, EyeSlash } from "phosphor-react-native";

import { AppText, Button, Field, useToast, haptic } from "@/src/components/ui";
import { Logo } from "@/src/components/Logo";
import { AuthProgress } from "@/src/components/AuthProgress";
import { useAuth } from "@/src/auth";
import { ApiError } from "@/src/api";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

// Mirrors the backend's `validate_password` exactly (server.py) — this is a UX preview only, the
// server independently re-validates and is the actual source of truth.
function checkPassword(pw: string) {
  return {
    length: pw.length >= 8,
    upper: /[A-Z]/.test(pw),
    lower: /[a-z]/.test(pw),
    number: /[0-9]/.test(pw),
    special: /[^A-Za-z0-9]/.test(pw),
  };
}

function RequirementRow({ met, label }: { met: boolean; label: string }) {
  const { colors } = useTheme();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
      <View
        style={{
          width: 16,
          height: 16,
          borderRadius: 8,
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: met ? colors.brandSecondary : "transparent",
          borderWidth: met ? 0 : 1.5,
          borderColor: colors.borderStrong,
        }}
      >
        {met && <Check size={10} color={colors.onBrandSecondary} weight="bold" />}
      </View>
      <AppText variant="body" color={met ? colors.onSurface : colors.muted}>
        {label}
      </AppText>
    </View>
  );
}

export default function CreatePassword() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const { t } = useLanguage();
  const { completeRegistration } = useAuth();
  const { email, token } = useLocalSearchParams<{ email: string; token: string }>();

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const checks = useMemo(() => checkPassword(password), [password]);
  const allMet = checks.length && checks.upper && checks.lower && checks.number && checks.special;
  const mismatch = confirm.length > 0 && password !== confirm;
  const canSubmit = allMet && confirm.length > 0 && !mismatch;

  const submit = async () => {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    try {
      await completeRegistration(String(email), password, String(token));
      haptic("success");
      // No manual navigation: the account is now signed in with onboarding_completed=false, and the
      // root navigator sends a user in that state straight to the Welcome screen.
    } catch (e) {
      haptic("light");
      const code = e instanceof ApiError ? e.code : undefined;
      if (code === "verification_expired") {
        toast(t("otp.errorExpired"), "error");
        router.replace("/(auth)/login");
      } else {
        toast(e instanceof Error ? e.message : t("auth.somethingWentWrong"), "error");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View style={styles.root}>
      <KeyboardAwareScrollView
        contentContainerStyle={[styles.content, { paddingTop: insets.top + 32, paddingBottom: insets.bottom + 24 }]}
        bottomOffset={24}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.brandWrap}>
          <Logo variant="full" height={40} />
        </View>

        <AuthProgress step="password" />

        <View style={styles.head}>
          <AppText variant="title" style={styles.title}>
            {t("createPassword.title")}
          </AppText>
          <AppText variant="body" color={colors.muted} style={styles.subtitle}>
            {t("createPassword.subtitle")}
          </AppText>
        </View>

        <View style={{ gap: 12 }}>
          <Field
            testID="new-password-input"
            label={t("createPassword.password")}
            placeholder="••••••••"
            value={password}
            onChangeText={setPassword}
            secureTextEntry={!showPw}
            autoCapitalize="none"
            autoCorrect={false}
            textContentType="newPassword"
            autoComplete="new-password"
            importantForAutofill="yes"
            rightElement={
              <Pressable
                testID="toggle-password-visibility"
                accessibilityLabel={showPw ? t("createPassword.hide") : t("createPassword.show")}
                onPress={() => setShowPw((s) => !s)}
                hitSlop={10}
              >
                {showPw ? <EyeSlash size={20} color={colors.muted} /> : <Eye size={20} color={colors.muted} />}
              </Pressable>
            }
          />

          <Field
            testID="confirm-password-input"
            label={t("createPassword.confirmPassword")}
            placeholder="••••••••"
            value={confirm}
            onChangeText={setConfirm}
            secureTextEntry={!showConfirm}
            autoCapitalize="none"
            autoCorrect={false}
            textContentType="newPassword"
            autoComplete="new-password"
            importantForAutofill="yes"
            rightElement={
              <Pressable
                testID="toggle-confirm-visibility"
                accessibilityLabel={showConfirm ? t("createPassword.hide") : t("createPassword.show")}
                onPress={() => setShowConfirm((s) => !s)}
                hitSlop={10}
              >
                {showConfirm ? <EyeSlash size={20} color={colors.muted} /> : <Eye size={20} color={colors.muted} />}
              </Pressable>
            }
          />

          {mismatch && (
            <AppText testID="password-mismatch" variant="caption" color={colors.error}>
              {t("createPassword.mismatch")}
            </AppText>
          )}
        </View>

        <View style={styles.requirements}>
          <AppText variant="label" color={colors.muted} style={{ marginBottom: 4 }}>
            {t("createPassword.requirementsTitle")}
          </AppText>
          <RequirementRow met={checks.length} label={t("createPassword.reqLength")} />
          <RequirementRow met={checks.upper} label={t("createPassword.reqUpper")} />
          <RequirementRow met={checks.lower} label={t("createPassword.reqLower")} />
          <RequirementRow met={checks.number} label={t("createPassword.reqNumber")} />
          <RequirementRow met={checks.special} label={t("createPassword.reqSpecial")} />
        </View>

        <Button
          testID="create-password-submit"
          title={t("createPassword.submit")}
          onPress={submit}
          loading={submitting}
          disabled={!canSubmit}
          style={{ marginTop: 4 }}
        />
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { paddingHorizontal: 24, gap: 20 },
  brandWrap: { alignItems: "center" },
  head: { gap: 8, alignItems: "center", marginTop: 4 },
  title: { textAlign: "center" },
  subtitle: { textAlign: "center", maxWidth: 300 },
  requirements: { gap: 8, backgroundColor: colors.glassFill, borderWidth: 1, borderColor: colors.glassBorder, borderRadius: 18, padding: 16 },
}));
