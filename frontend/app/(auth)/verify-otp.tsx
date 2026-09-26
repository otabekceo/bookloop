import { useEffect, useRef, useState } from "react";
import { Pressable, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";

import { AppText, Button, useToast, haptic } from "@/src/components/ui";
import { Logo } from "@/src/components/Logo";
import { AuthProgress } from "@/src/components/AuthProgress";
import { useAuth } from "@/src/auth";
import { ApiError } from "@/src/api";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

const OTP_LENGTH = 6;

/** Maps the backend's stable error `code` to a translated message; falls back to its English `message`
 * for anything not recognized (e.g. a network error, which has no code at all). */
function otpErrorMessage(e: unknown, t: (k: string) => string): string {
  const code = e instanceof ApiError ? e.code : undefined;
  const key: Record<string, string> = {
    invalid_otp: "otp.errorInvalid",
    otp_expired: "otp.errorExpired",
    too_many_attempts: "otp.errorTooManyAttempts",
    rate_limited: "otp.errorRateLimited",
  };
  if (code && key[code]) return t(key[code]);
  return e instanceof Error ? e.message : t("auth.somethingWentWrong");
}

export default function VerifyOtp() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const { t, isRTL } = useLanguage();
  const { verifyOtp, resendOtp } = useAuth();
  const { email, resendAfter } = useLocalSearchParams<{ email: string; resendAfter?: string }>();

  const [code, setCode] = useState("");
  const [focused, setFocused] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [resending, setResending] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(Number(resendAfter) || 60);
  const inputRef = useRef<TextInput>(null);

  useEffect(() => {
    if (secondsLeft <= 0) return;
    const id = setInterval(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(id);
  }, [secondsLeft > 0]); // eslint-disable-line react-hooks/exhaustive-deps -- restart the tick only when it (re)starts

  const submit = async (fullCode: string) => {
    if (fullCode.length !== OTP_LENGTH || verifying) return;
    setVerifying(true);
    try {
      const result = await verifyOtp(String(email), fullCode);
      haptic("success");
      router.push({
        pathname: "/(auth)/create-password",
        params: { email: String(email), token: result.verification_token },
      });
    } catch (e) {
      haptic("light");
      setCode("");
      toast(otpErrorMessage(e, t), "error");
    } finally {
      setVerifying(false);
    }
  };

  const onChangeCode = (value: string) => {
    const digits = value.replace(/[^0-9]/g, "").slice(0, OTP_LENGTH);
    setCode(digits);
    if (digits.length === OTP_LENGTH) submit(digits);
  };

  const resend = async () => {
    if (secondsLeft > 0 || resending) return;
    setResending(true);
    try {
      const result = await resendOtp(String(email));
      setCode("");
      setSecondsLeft(result.resend_after_seconds);
      haptic("light");
      toast(t("otp.resent"), "success");
      inputRef.current?.focus();
    } catch (e) {
      toast(otpErrorMessage(e, t), "error");
    } finally {
      setResending(false);
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

        <AuthProgress step="verify" />

        <View style={styles.head}>
          <AppText variant="title" style={styles.title}>
            {t("otp.title")}
          </AppText>
          <AppText variant="body" color={colors.muted} style={styles.subtitle}>
            {t("otp.subtitle", { email })}
          </AppText>
        </View>

        <Pressable
          testID="otp-boxes"
          onPress={() => inputRef.current?.focus()}
          style={[styles.boxRow, isRTL && { flexDirection: "row-reverse" }]}
        >
          {Array.from({ length: OTP_LENGTH }).map((_, i) => {
            const filled = i < code.length;
            const isActive = focused && i === code.length;
            return (
              <View
                key={i}
                style={[
                  styles.box,
                  { borderColor: isActive ? colors.brand : colors.glassBorder },
                  filled && { borderColor: colors.glassBorder },
                ]}
              >
                <AppText variant="title" style={styles.boxDigit}>
                  {code[i] || ""}
                </AppText>
              </View>
            );
          })}
          <TextInput
            ref={inputRef}
            testID="otp-hidden-input"
            accessibilityLabel={t("otp.codeLabel")}
            value={code}
            onChangeText={onChangeCode}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            keyboardType="number-pad"
            maxLength={OTP_LENGTH}
            textContentType="oneTimeCode"
            autoComplete="one-time-code"
            autoFocus
            style={styles.hiddenInput}
          />
        </Pressable>

        <Button
          testID="otp-verify-button"
          title={t("otp.verify")}
          onPress={() => submit(code)}
          loading={verifying}
          disabled={code.length !== OTP_LENGTH}
          style={{ marginTop: 8 }}
        />

        <Pressable testID="otp-resend-button" onPress={resend} disabled={secondsLeft > 0 || resending} style={styles.resendWrap}>
          <AppText variant="label" color={secondsLeft > 0 ? colors.muted : colors.brandPrimary}>
            {secondsLeft > 0 ? t("otp.resendIn", { seconds: secondsLeft }) : t("otp.resend")}
          </AppText>
        </Pressable>
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
  boxRow: { flexDirection: "row", justifyContent: "center", gap: 10, marginTop: 8 },
  box: {
    width: 46,
    height: 56,
    borderRadius: 14,
    borderWidth: 1.5,
    backgroundColor: colors.glassFill,
    alignItems: "center",
    justifyContent: "center",
  },
  boxDigit: { fontSize: 22 },
  // The real input: invisible but present and focusable, positioned over the boxes so taps land on it.
  hiddenInput: { position: "absolute", opacity: 0, width: "100%", height: "100%" },
  resendWrap: { alignSelf: "center", marginTop: 4, padding: 8 },
}));
