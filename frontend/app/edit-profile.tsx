import { useState } from "react";
import { View, Pressable, ActivityIndicator, Platform } from "react-native";
import { useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useQueryClient } from "@tanstack/react-query";
import { X, Camera, Image as ImageIcon, MapPin } from "phosphor-react-native";

import { AppText, Avatar, Button, Field, Chip, haptic, useToast, useDirectionalStyle } from "@/src/components/ui";
import { useAuth } from "@/src/auth";
import { apiFetch } from "@/src/api";
import { pickImage, uploadWithProgress, cameraDeniedAlert } from "@/src/media";
import { GENRES, LANGUAGES } from "@/src/constants";
import { enumLabel } from "@/src/i18n/enums";
import { useDeviceLocation } from "@/src/location";
import { LocationPrompt } from "@/src/components/LocationPrompt";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";

export default function EditProfile() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const { t } = useLanguage();
  const { user, refreshUser } = useAuth();
  // Camera badge sits on the avatar's trailing corner; mirror it under RTL.
  const camBadgeStyle = useDirectionalStyle(styles.camBadge);

  const [name, setName] = useState(user?.name || "");
  const [bio, setBio] = useState(user?.bio || "");
  const [avatar, setAvatar] = useState<string | null>(user?.avatar_url || null);
  // Location is not editable here: it only ever comes from the device (see src/location.ts).
  const location = useDeviceLocation();
  const [genres, setGenres] = useState<string[]>(user?.genres || []);
  const [languages, setLanguages] = useState<string[]>(user?.languages || []);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);

  const toggle = (arr: string[], set: (v: string[]) => void, v: string) => {
    haptic("selection");
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  };

  const pickAvatar = async (source: "library" | "camera") => {
    const picked = await pickImage(source, (blocked) => cameraDeniedAlert(t, blocked, () => pickAvatar(source)));
    if (!picked) return;
    setUploading(true);
    try {
      const up = await uploadWithProgress(picked.uri);
      setAvatar(up.url);
    } catch (e: any) {
      toast(e.message || t("editProfile.uploadFailed"), "error");
    } finally {
      setUploading(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiFetch("/api/users/me", {
        method: "PUT",
        body: { name, bio, avatar_url: avatar, genres, languages },
      });
      await refreshUser();
      qc.invalidateQueries();
      haptic("success");
      toast(t("editProfile.profileUpdated"), "success");
      router.back();
    } catch (e: any) {
      toast(e.message || t("editProfile.couldNotSave"), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="close-edit" onPress={() => router.back()} style={styles.iconBtn}>
          <X size={22} color={colors.onSurface} />
        </Pressable>
        <AppText variant="heading">{t("editProfile.title")}</AppText>
        <View style={{ width: 42 }} />
      </View>

      <KeyboardAwareScrollView contentContainerStyle={{ padding: 20, gap: 18, paddingBottom: 40 }} bottomOffset={90} showsVerticalScrollIndicator={false}>
        <View style={{ alignItems: "center", gap: 10 }}>
          <Pressable testID="pick-avatar" onPress={() => pickAvatar("library")} style={styles.avatarWrap}>
            {uploading ? <ActivityIndicator color={colors.brandPrimary} /> : <Avatar uri={avatar} name={name} size={96} />}
            <View style={camBadgeStyle}>
              <Camera size={16} color={colors.onBrandPrimary} weight="fill" />
            </View>
          </Pressable>
          <View style={{ flexDirection: "row", gap: 10 }}>
            <Pressable testID="avatar-gallery" onPress={() => pickAvatar("library")} style={styles.smallBtn}>
              <ImageIcon size={16} color={colors.onSurface} />
              <AppText variant="label">{t("editProfile.gallery")}</AppText>
            </Pressable>
            {Platform.OS !== "web" && (
              <Pressable testID="avatar-camera" onPress={() => pickAvatar("camera")} style={styles.smallBtn}>
                <Camera size={16} color={colors.onSurface} />
                <AppText variant="label">{t("editProfile.camera")}</AppText>
              </Pressable>
            )}
          </View>
        </View>

        <Field testID="edit-name" label={t("editProfile.name")} value={name} onChangeText={setName} onSurface />
        <Field testID="edit-bio" label={t("editProfile.bio")} value={bio} onChangeText={setBio} onSurface multiline style={{ minHeight: 70, textAlignVertical: "top" }} />

        <View style={{ gap: 10 }}>
          <AppText variant="label">{t("editProfile.neighborhood")}</AppText>
          {location.hasLocation ? (
            <View style={styles.locationRow} testID="edit-location">
              <MapPin size={18} color={colors.brandSecondary} weight="fill" />
              <View style={{ flex: 1, gap: 2 }}>
                <AppText variant="body">
                  {[user?.neighborhood, user?.city].filter(Boolean).join(", ") || t("location.detected")}
                </AppText>
                <AppText variant="caption" color={colors.muted}>
                  {t("location.fromDevice")}
                </AppText>
              </View>
              <Button
                testID="edit-location-refresh"
                variant="outline"
                title={t("location.update")}
                loading={location.state === "locating"}
                onPress={location.request}
                style={{ height: 42, paddingHorizontal: 14, flexShrink: 0 }}
              />
            </View>
          ) : (
            <LocationPrompt state={location.state} onRequest={location.request} onOpenSettings={location.openSettings} testID="edit-location-prompt" />
          )}
          {location.hasLocation && (location.state === "blocked" || location.state === "denied" || location.state === "servicesOff" || location.state === "error") && (
            <AppText variant="caption" color={colors.muted}>
              {location.state === "blocked"
                ? t("location.blockedBody")
                : location.state === "denied"
                  ? t("location.deniedBody")
                  : location.state === "servicesOff"
                    ? t("location.servicesOffBody")
                    : t("location.errorBody")}
            </AppText>
          )}
        </View>

        <View style={{ gap: 10 }}>
          <AppText variant="label">{t("editProfile.interestedGenres")}</AppText>
          <View style={styles.wrap}>
            {GENRES.map((g) => (
              <Chip key={g} label={enumLabel(t, "genre", g)} selected={genres.includes(g)} onPress={() => toggle(genres, setGenres, g)} testID={`pg-${g}`} />
            ))}
          </View>
        </View>

        <View style={{ gap: 10 }}>
          <AppText variant="label">{t("editProfile.languages")}</AppText>
          <View style={styles.wrap}>
            {/* Offered languages, plus any older value the reader still has selected so it can be removed. */}
            {[...LANGUAGES, ...languages.filter((l) => !LANGUAGES.includes(l))].map((l) => (
              <Chip key={l} label={enumLabel(t, "bookLanguage", l)} selected={languages.includes(l)} onPress={() => toggle(languages, setLanguages, l)} testID={`pl-${l}`} />
            ))}
          </View>
        </View>

        <Button testID="save-profile" title={t("editProfile.saveProfile")} onPress={save} loading={saving} style={{ marginTop: 8 }} />
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: colors.border },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  avatarWrap: { width: 96, height: 96 },
  smallBtn: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 16, paddingVertical: 8, borderRadius: 12 },
  camBadge: { position: "absolute", bottom: 0, right: 0, width: 30, height: 30, borderRadius: 15, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: colors.surface },
  wrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  locationRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, borderRadius: 16, padding: 14 },
}));
