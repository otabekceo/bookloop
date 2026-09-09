import { useState } from "react";
import { View, Pressable, ActivityIndicator, Linking } from "react-native";
import { useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useQueryClient } from "@tanstack/react-query";
import * as ImagePicker from "expo-image-picker";
import { Image } from "expo-image";
import { X, Camera, Plus } from "phosphor-react-native";

import { AppText, Button, Field, Chip, haptic, useToast } from "@/src/components/ui";
import { apiFetch, uploadImage, resolveImage } from "@/src/api";
import { CONDITIONS, GENRES, LANGUAGES } from "@/src/constants";
import { makeStyles, useTheme } from "@/src/theme";

export default function AddBook() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();

  const [coverUrl, setCoverUrl] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [condition, setCondition] = useState("Good");
  const [genre, setGenre] = useState("Fiction");
  const [language, setLanguage] = useState("English");
  const [saving, setSaving] = useState(false);

  const pickImage = async () => {
    haptic("light");
    const perm = await ImagePicker.getMediaLibraryPermissionsAsync();
    let status = perm.status;
    if (status !== "granted") {
      if (perm.canAskAgain) {
        const req = await ImagePicker.requestMediaLibraryPermissionsAsync();
        status = req.status;
      }
      if (status !== "granted") {
        toast("Enable photo access in Settings to add a cover", "error");
        if (!perm.canAskAgain) Linking.openSettings();
        return;
      }
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      allowsEditing: true,
      aspect: [2, 3],
      quality: 0.7,
    });
    if (result.canceled || !result.assets?.[0]) return;
    setUploading(true);
    try {
      const up = await uploadImage(result.assets[0].uri);
      setCoverUrl(up.url);
    } catch {
      toast("Upload failed, try again", "error");
    } finally {
      setUploading(false);
    }
  };

  const save = async () => {
    if (!title.trim()) {
      toast("Please add a title", "error");
      return;
    }
    setSaving(true);
    try {
      await apiFetch("/api/books", {
        method: "POST",
        body: { title: title.trim(), author: author.trim(), cover_url: coverUrl, condition, genre, language, status: "Available" },
      });
      haptic("success");
      qc.invalidateQueries({ queryKey: ["myBooks"] });
      toast("Book added to your shelf", "success");
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
        <Pressable testID="close-add-book" onPress={() => router.back()} style={styles.iconBtn}>
          <X size={22} color={colors.onSurface} />
        </Pressable>
        <AppText variant="heading">Add a book</AppText>
        <View style={{ width: 42 }} />
      </View>

      <KeyboardAwareScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: 20, paddingBottom: 40, gap: 20 }}
        bottomOffset={90}
        showsVerticalScrollIndicator={false}
      >
        <View style={{ alignItems: "center" }}>
          <Pressable testID="pick-cover" onPress={pickImage} style={styles.cover}>
            {uploading ? (
              <ActivityIndicator color={colors.brandPrimary} />
            ) : coverUrl ? (
              <Image source={{ uri: resolveImage(coverUrl) }} style={styles.coverImg} contentFit="cover" />
            ) : (
              <View style={{ alignItems: "center", gap: 8 }}>
                <Camera size={30} color={colors.muted} />
                <AppText variant="label" color={colors.muted}>
                  Add cover photo
                </AppText>
              </View>
            )}
          </Pressable>
        </View>

        <Field testID="title-input" label="Title" placeholder="e.g. Atomic Habits" value={title} onChangeText={setTitle} onSurface />
        <Field testID="author-input" label="Author" placeholder="e.g. James Clear" value={author} onChangeText={setAuthor} onSurface />

        <View style={{ gap: 10 }}>
          <AppText variant="label">Condition</AppText>
          <View style={styles.chipWrap}>
            {CONDITIONS.map((c) => (
              <Chip key={c} label={c} selected={condition === c} onPress={() => setCondition(c)} testID={`condition-${c}`} />
            ))}
          </View>
        </View>

        <View style={{ gap: 10 }}>
          <AppText variant="label">Genre</AppText>
          <View style={styles.chipWrap}>
            {GENRES.map((g) => (
              <Chip key={g} label={g} selected={genre === g} onPress={() => setGenre(g)} testID={`add-genre-${g}`} />
            ))}
          </View>
        </View>

        <View style={{ gap: 10 }}>
          <AppText variant="label">Language</AppText>
          <View style={styles.chipWrap}>
            {LANGUAGES.map((l) => (
              <Chip key={l} label={l} selected={language === l} onPress={() => setLanguage(l)} testID={`add-lang-${l}`} />
            ))}
          </View>
        </View>
      </KeyboardAwareScrollView>

      <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>
        <Button testID="save-book-button" title="Add to my shelf" onPress={save} loading={saving} icon={<Plus size={18} color={colors.onBrandPrimary} weight="bold" />} />
      </View>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: colors.border },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  cover: { width: 140, height: 210, borderRadius: 12, backgroundColor: colors.surfaceTertiary, borderWidth: 1, borderColor: colors.borderStrong, borderStyle: "dashed", alignItems: "center", justifyContent: "center", overflow: "hidden" },
  coverImg: { width: "100%", height: "100%" },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  footer: { paddingHorizontal: 20, paddingTop: 12, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border },
}));
