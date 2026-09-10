import { useEffect, useState } from "react";
import { View, Pressable, ActivityIndicator, FlatList, Platform } from "react-native";
import { useRouter, Stack, useLocalSearchParams } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useQueryClient } from "@tanstack/react-query";
import { X, Camera, Image as ImageIcon, Plus, MagnifyingGlass, Barcode, PencilSimple } from "phosphor-react-native";

import { AppText, Button, Field, Chip, BookCover, haptic, useToast } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { pickImage, uploadWithProgress, openSettings } from "@/src/media";
import { searchBooks, BookResult } from "@/src/googlebooks";
import { CONDITIONS, GENRES, LANGUAGES } from "@/src/constants";
import { makeStyles, useTheme } from "@/src/theme";

type Mode = "search" | "scan" | "manual";

export default function AddBook() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const params = useLocalSearchParams<{ prefill?: string }>();

  const [mode, setMode] = useState<Mode>("search");
  const [coverUrl, setCoverUrl] = useState<string | null>(null);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [isbn, setIsbn] = useState<string | null>(null);
  const [condition, setCondition] = useState("Good");
  const [genre, setGenre] = useState("Fiction");
  const [language, setLanguage] = useState("English");
  const [saving, setSaving] = useState(false);

  // Search state
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<BookResult[]>([]);
  const [searching, setSearching] = useState(false);

  // Prefill from barcode scan
  useEffect(() => {
    if (params.prefill) {
      try {
        const p = JSON.parse(params.prefill as string);
        if (p.title) setTitle(p.title);
        if (p.author) setAuthor(p.author);
        if (p.cover_url) setCoverUrl(p.cover_url);
        if (p.isbn) setIsbn(p.isbn);
        if (p.language) setLanguage(p.language);
        setMode("manual");
        toast(p.title ? "Book found — review & save" : "Add the details below", "success");
      } catch {}
    }
  }, [params.prefill]);

  // Debounced search
  useEffect(() => {
    if (mode !== "search" || query.trim().length < 2) {
      setResults([]);
      return;
    }
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        setResults(await searchBooks(query));
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 450);
    return () => clearTimeout(t);
  }, [query, mode]);

  const selectResult = (r: BookResult) => {
    haptic("selection");
    setTitle(r.title);
    setAuthor(r.author);
    setCoverUrl(r.cover_url || null);
    setIsbn(r.isbn || null);
    if (r.language) setLanguage(r.language);
    setMode("manual");
  };

  const chooseImage = async (source: "library" | "camera") => {
    haptic("light");
    const picked = await pickImage(source, (blocked) => {
      toast(blocked ? "Enable access in Settings" : "Permission needed to add a photo", "error");
      if (blocked) openSettings();
    });
    if (!picked) return;
    setUploadPct(0);
    try {
      const up = await uploadWithProgress(picked.uri, (f) => setUploadPct(f));
      setCoverUrl(up.url);
      toast("Cover uploaded", "success");
    } catch (e: any) {
      toast(e.message || "Upload failed", "error");
    } finally {
      setUploadPct(null);
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
        body: { title: title.trim(), author: author.trim(), cover_url: coverUrl, isbn, condition, genre, language, status: "Available" },
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

      {/* Mode segmented */}
      <View style={styles.segment}>
        {([
          { k: "search", label: "Search", icon: MagnifyingGlass },
          { k: "scan", label: "Scan", icon: Barcode },
          { k: "manual", label: "Manual", icon: PencilSimple },
        ] as const).map((m) => {
          const active = mode === m.k;
          const Icon = m.icon;
          return (
            <Pressable
              key={m.k}
              testID={`mode-${m.k}`}
              onPress={() => {
                haptic("selection");
                setMode(m.k);
              }}
              style={[styles.segItem, active && styles.segItemActive]}
            >
              <Icon size={16} color={active ? colors.onSurfaceInverse : colors.muted} weight={active ? "fill" : "regular"} />
              <AppText variant="label" color={active ? colors.onSurfaceInverse : colors.muted}>
                {m.label}
              </AppText>
            </Pressable>
          );
        })}
      </View>

      {mode === "search" && (
        <View style={{ flex: 1 }}>
          <View style={styles.searchWrap}>
            <View style={styles.searchPill}>
              <MagnifyingGlass size={18} color={colors.muted} />
              <Field
                testID="book-search-input"
                placeholder="Title, author or ISBN"
                value={query}
                onChangeText={setQuery}
                autoFocus
                style={styles.searchInput}
              />
              {searching && <ActivityIndicator color={colors.brandPrimary} />}
            </View>
          </View>
          <FlatList
            data={results}
            keyExtractor={(r, i) => `${r.isbn || r.title}-${i}`}
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={{ padding: 20, paddingTop: 4, gap: 12, paddingBottom: 40 }}
            renderItem={({ item }) => (
              <Pressable testID="search-result" onPress={() => selectResult(item)} style={styles.resultRow}>
                <BookCover uri={item.cover_url} width={44} title={item.title} />
                <View style={{ flex: 1 }}>
                  <AppText variant="label" numberOfLines={2}>{item.title}</AppText>
                  <AppText variant="caption" color={colors.muted} numberOfLines={1}>{item.author || "Unknown author"}</AppText>
                </View>
                <Plus size={20} color={colors.brandPrimary} weight="bold" />
              </Pressable>
            )}
            ListEmptyComponent={
              query.trim().length >= 2 && !searching ? (
                <AppText variant="body" color={colors.muted} style={{ textAlign: "center", marginTop: 30 }}>
                  No matches. Try Manual entry.
                </AppText>
              ) : (
                <AppText variant="body" color={colors.muted} style={{ textAlign: "center", marginTop: 30 }}>
                  Search millions of books to auto-fill the cover & author.
                </AppText>
              )
            }
          />
        </View>
      )}

      {mode === "scan" && (
        <View style={styles.scanPane}>
          <View style={styles.scanIcon}>
            <Barcode size={40} color={colors.brandSecondary} weight="light" />
          </View>
          <AppText variant="title" style={{ textAlign: "center" }}>Scan the barcode</AppText>
          <AppText variant="body" color={colors.muted} style={{ textAlign: "center", maxWidth: 280 }}>
            Point your camera at the ISBN barcode on the back cover to add a book instantly.
          </AppText>
          <Button
            testID="open-scanner"
            title="Open camera scanner"
            icon={<Camera size={18} color={colors.onBrandPrimary} weight="fill" />}
            onPress={() => router.push("/book/scan")}
            style={{ paddingHorizontal: 28 }}
          />
        </View>
      )}

      {mode === "manual" && (
        <>
          <KeyboardAwareScrollView
            style={{ flex: 1 }}
            contentContainerStyle={{ padding: 20, paddingBottom: 40, gap: 18 }}
            bottomOffset={90}
            showsVerticalScrollIndicator={false}
          >
            <View style={{ alignItems: "center", gap: 10 }}>
              <View style={styles.coverBox}>
                <BookCover uri={coverUrl} width={130} title={title} />
                {uploadPct !== null && (
                  <View style={styles.uploadOverlay}>
                    <ActivityIndicator color="#FFFFFF" />
                    <AppText variant="caption" color="#FFFFFF">{Math.round(uploadPct * 100)}%</AppText>
                  </View>
                )}
              </View>
              <View style={{ flexDirection: "row", gap: 10 }}>
                <Pressable testID="cover-gallery" onPress={() => chooseImage("library")} style={styles.smallBtn}>
                  <ImageIcon size={16} color={colors.onSurface} />
                  <AppText variant="label">Gallery</AppText>
                </Pressable>
                {Platform.OS !== "web" && (
                  <Pressable testID="cover-camera" onPress={() => chooseImage("camera")} style={styles.smallBtn}>
                    <Camera size={16} color={colors.onSurface} />
                    <AppText variant="label">Camera</AppText>
                  </Pressable>
                )}
              </View>
              <AppText variant="caption" color={colors.muted}>No cover? We'll create a nice one for you.</AppText>
            </View>

            <Field testID="title-input" label="Title" placeholder="e.g. Atomic Habits" value={title} onChangeText={setTitle} onSurface />
            <Field testID="author-input" label="Author" placeholder="e.g. James Clear" value={author} onChangeText={setAuthor} onSurface />

            <ChipGroup label="Condition" options={CONDITIONS} value={condition} onChange={setCondition} idPrefix="condition" />
            <ChipGroup label="Genre" options={GENRES} value={genre} onChange={setGenre} idPrefix="add-genre" />
            <ChipGroup label="Language" options={LANGUAGES} value={language} onChange={setLanguage} idPrefix="add-lang" />
          </KeyboardAwareScrollView>
          <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>
            <Button testID="save-book-button" title="Add to my shelf" onPress={save} loading={saving} icon={<Plus size={18} color={colors.onBrandPrimary} weight="bold" />} />
          </View>
        </>
      )}
    </View>
  );
}

function ChipGroup({ label, options, value, onChange, idPrefix }: { label: string; options: string[]; value: string; onChange: (v: string) => void; idPrefix: string }) {
  const styles = useStyles();
  return (
    <View style={{ gap: 10 }}>
      <AppText variant="label">{label}</AppText>
      <View style={styles.chipWrap}>
        {options.map((o) => (
          <Chip key={o} label={o} selected={value === o} onPress={() => onChange(o)} testID={`${idPrefix}-${o}`} />
        ))}
      </View>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: colors.border },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  segment: { flexDirection: "row", gap: 8, paddingHorizontal: 20, paddingVertical: 14 },
  segItem: { flex: 1, flexDirection: "row", gap: 6, alignItems: "center", justifyContent: "center", paddingVertical: 10, borderRadius: 12, backgroundColor: colors.surfaceTertiary },
  segItemActive: { backgroundColor: colors.surfaceInverse },
  searchWrap: { paddingHorizontal: 20 },
  searchPill: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surfaceSecondary, borderRadius: 14, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 14 },
  searchInput: { flex: 1, borderWidth: 0, backgroundColor: "transparent", paddingLeft: 0, paddingVertical: 12 },
  resultRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.surfaceSecondary, borderRadius: 14, padding: 10, borderWidth: 1, borderColor: colors.border },
  scanPane: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14, padding: 30 },
  scanIcon: { width: 80, height: 80, borderRadius: 40, backgroundColor: colors.sageSoft, alignItems: "center", justifyContent: "center" },
  coverBox: { width: 130, height: 195 },
  uploadOverlay: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: "rgba(23,33,31,0.55)", borderRadius: 8, alignItems: "center", justifyContent: "center", gap: 6 },
  smallBtn: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 16, paddingVertical: 10, borderRadius: 12 },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  footer: { paddingHorizontal: 20, paddingTop: 12, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border },
}));
