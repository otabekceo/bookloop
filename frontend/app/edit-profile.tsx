import { useState } from "react";
import { View, Pressable, ActivityIndicator, Platform } from "react-native";
import { useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useQueryClient } from "@tanstack/react-query";
import { X, Camera, Image as ImageIcon } from "phosphor-react-native";

import { AppText, Avatar, Button, Field, Chip, haptic, useToast } from "@/src/components/ui";
import { useAuth } from "@/src/auth";
import { apiFetch } from "@/src/api";
import { pickImage, uploadWithProgress, openSettings } from "@/src/media";
import { GENRES, LANGUAGES } from "@/src/constants";
import { makeStyles, useTheme } from "@/src/theme";

const NB_COORDS: Record<string, { lat: number; lng: number }> = {
  Centro: { lat: 38.1938, lng: 15.554 },
  "University Area": { lat: 38.249, lng: 15.556 },
  Annunziata: { lat: 38.247, lng: 15.547 },
  Giostra: { lat: 38.21, lng: 15.547 },
  Tremestieri: { lat: 38.12, lng: 15.52 },
  Provinciale: { lat: 38.185, lng: 15.545 },
};

export default function EditProfile() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const { user, refreshUser } = useAuth();

  const [name, setName] = useState(user?.name || "");
  const [bio, setBio] = useState(user?.bio || "");
  const [avatar, setAvatar] = useState<string | null>(user?.avatar_url || null);
  const [neighborhood, setNeighborhood] = useState(user?.neighborhood || "Centro");
  const [genres, setGenres] = useState<string[]>(user?.genres || []);
  const [languages, setLanguages] = useState<string[]>(user?.languages || []);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);

  const toggle = (arr: string[], set: (v: string[]) => void, v: string) => {
    haptic("selection");
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  };

  const pickAvatar = async (source: "library" | "camera") => {
    const picked = await pickImage(source, (blocked) => {
      toast(blocked ? "Enable photo access in Settings" : "Permission needed", "error");
      if (blocked) openSettings();
    });
    if (!picked) return;
    setUploading(true);
    try {
      const up = await uploadWithProgress(picked.uri);
      setAvatar(up.url);
    } catch (e: any) {
      toast(e.message || "Upload failed", "error");
    } finally {
      setUploading(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const coords = NB_COORDS[neighborhood] || NB_COORDS.Centro;
      await apiFetch("/api/users/me", {
        method: "PUT",
        body: { name, bio, avatar_url: avatar, neighborhood, genres, languages, lat: coords.lat, lng: coords.lng, city: "Messina" },
      });
      await refreshUser();
      qc.invalidateQueries();
      haptic("success");
      toast("Profile updated", "success");
      router.back();
    } catch (e: any) {
      toast(e.message || "Could not save", "error");
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
        <AppText variant="heading">Edit profile</AppText>
        <View style={{ width: 42 }} />
      </View>

      <KeyboardAwareScrollView contentContainerStyle={{ padding: 20, gap: 18, paddingBottom: 40 }} bottomOffset={90} showsVerticalScrollIndicator={false}>
        <View style={{ alignItems: "center", gap: 10 }}>
          <Pressable testID="pick-avatar" onPress={() => pickAvatar("library")} style={styles.avatarWrap}>
            {uploading ? <ActivityIndicator color={colors.brandPrimary} /> : <Avatar uri={avatar} name={name} size={96} />}
            <View style={styles.camBadge}>
              <Camera size={16} color={colors.onBrandPrimary} weight="fill" />
            </View>
          </Pressable>
          <View style={{ flexDirection: "row", gap: 10 }}>
            <Pressable testID="avatar-gallery" onPress={() => pickAvatar("library")} style={styles.smallBtn}>
              <ImageIcon size={16} color={colors.onSurface} />
              <AppText variant="label">Gallery</AppText>
            </Pressable>
            {Platform.OS !== "web" && (
              <Pressable testID="avatar-camera" onPress={() => pickAvatar("camera")} style={styles.smallBtn}>
                <Camera size={16} color={colors.onSurface} />
                <AppText variant="label">Camera</AppText>
              </Pressable>
            )}
          </View>
        </View>

        <Field testID="edit-name" label="Name" value={name} onChangeText={setName} onSurface />
        <Field testID="edit-bio" label="Bio" value={bio} onChangeText={setBio} onSurface multiline style={{ minHeight: 70, textAlignVertical: "top" }} />

        <View style={{ gap: 10 }}>
          <AppText variant="label">Neighborhood</AppText>
          <View style={styles.wrap}>
            {Object.keys(NB_COORDS).map((n) => (
              <Chip key={n} label={n} selected={neighborhood === n} onPress={() => setNeighborhood(n)} testID={`nb-${n}`} />
            ))}
          </View>
        </View>

        <View style={{ gap: 10 }}>
          <AppText variant="label">Interested genres</AppText>
          <View style={styles.wrap}>
            {GENRES.map((g) => (
              <Chip key={g} label={g} selected={genres.includes(g)} onPress={() => toggle(genres, setGenres, g)} testID={`pg-${g}`} />
            ))}
          </View>
        </View>

        <View style={{ gap: 10 }}>
          <AppText variant="label">Languages</AppText>
          <View style={styles.wrap}>
            {LANGUAGES.map((l) => (
              <Chip key={l} label={l} selected={languages.includes(l)} onPress={() => toggle(languages, setLanguages, l)} testID={`pl-${l}`} />
            ))}
          </View>
        </View>

        <Button testID="save-profile" title="Save profile" onPress={save} loading={saving} style={{ marginTop: 8 }} />
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
}));
