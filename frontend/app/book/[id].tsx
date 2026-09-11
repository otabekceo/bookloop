import { useEffect, useState } from "react";
import { View, ScrollView, Pressable, ActivityIndicator } from "react-native";
import { useLocalSearchParams, useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Trash, MapPin, ArrowsClockwise, Fire } from "phosphor-react-native";

import { AppText, Avatar, Button, Field, Chip, BookCover, StatusBadge, RatingPill, haptic, useToast } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { CONDITIONS, GENRES, LANGUAGES, BOOK_STATUSES } from "@/src/constants";
import { makeStyles, useTheme } from "@/src/theme";

export default function BookDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["book", id],
    queryFn: () => apiFetch<{ book: any; owner: any; is_owner: boolean; wanted_by: number }>(`/api/books/detail/${id}`),
  });

  const [form, setForm] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (data?.book) setForm(data.book);
  }, [data]);

  const isOwner = data?.is_owner;

  const save = async () => {
    setSaving(true);
    try {
      await apiFetch(`/api/books/${id}`, {
        method: "PUT",
        body: {
          title: form.title,
          author: form.author,
          cover_url: form.cover_url,
          condition: form.condition,
          genre: form.genre,
          language: form.language,
          status: form.status,
        },
      });
      haptic("success");
      qc.invalidateQueries({ queryKey: ["myBooks"] });
      toast("Saved", "success");
      router.back();
    } catch (e: any) {
      toast(e.message || "Could not save", "error");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    try {
      await apiFetch(`/api/books/${id}`, { method: "DELETE" });
      qc.invalidateQueries({ queryKey: ["myBooks"] });
      toast("Book removed", "success");
      router.back();
    } catch {
      toast("Could not delete", "error");
    }
  };

  const startSwap = async () => {
    haptic("light");
    try {
      const res = await apiFetch<{ swap: { id: string } }>("/api/swaps", {
        method: "POST",
        body: { receiver_id: data!.owner.user_id },
      });
      router.push(`/swap/${res.swap.id}`);
    } catch (e: any) {
      toast(e.message || "Could not start swap", "error");
    }
  };

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.iconBtn}>
          <ArrowLeft size={22} color={colors.onSurface} />
        </Pressable>
        <AppText variant="heading">{isOwner ? "Edit book" : "Book"}</AppText>
        {isOwner ? (
          <Pressable testID="delete-book" onPress={remove} style={styles.iconBtn}>
            <Trash size={20} color={colors.error} />
          </Pressable>
        ) : (
          <View style={{ width: 42 }} />
        )}
      </View>

      {isLoading || !data || !form ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 60 }} />
      ) : isOwner ? (
        <>
          <KeyboardAwareScrollView contentContainerStyle={{ padding: 20, gap: 18, paddingBottom: 40 }} bottomOffset={90} showsVerticalScrollIndicator={false}>
            <View style={{ alignItems: "center", gap: 10 }}>
              <BookCover uri={form.cover_url} width={130} />
              {(data.wanted_by || 0) > 0 && (
                <View testID="wanted-by" style={styles.wantedPill}>
                  <Fire size={14} color={colors.onBrandTertiary} weight="fill" />
                  <AppText variant="caption" color={colors.onBrandTertiary}>
                    {data.wanted_by} {data.wanted_by === 1 ? "reader" : "readers"} nearby {data.wanted_by === 1 ? "is" : "are"} looking for {form.genre} books
                  </AppText>
                </View>
              )}
            </View>
            <Field label="Title" value={form.title} onChangeText={(t) => setForm({ ...form, title: t })} onSurface testID="edit-title" />
            <Field label="Author" value={form.author} onChangeText={(t) => setForm({ ...form, author: t })} onSurface testID="edit-author" />
            <EditGroup label="Status" options={BOOK_STATUSES} value={form.status} onChange={(v) => setForm({ ...form, status: v })} />
            <EditGroup label="Condition" options={CONDITIONS} value={form.condition} onChange={(v) => setForm({ ...form, condition: v })} />
            <EditGroup label="Genre" options={GENRES} value={form.genre} onChange={(v) => setForm({ ...form, genre: v })} />
            <EditGroup label="Language" options={LANGUAGES} value={form.language} onChange={(v) => setForm({ ...form, language: v })} />
          </KeyboardAwareScrollView>
          <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>
            <Button testID="save-book-edit" title="Save changes" onPress={save} loading={saving} />
          </View>
        </>
      ) : (
        <>
          <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: 120 }} showsVerticalScrollIndicator={false}>
            <View style={{ alignItems: "center", gap: 12 }}>
              <BookCover uri={form.cover_url} width={160} />
              <AppText variant="title" style={{ textAlign: "center" }}>
                {form.title}
              </AppText>
              <AppText variant="body" color={colors.muted}>
                {form.author}
              </AppText>
              <View style={styles.badgeRow}>
                <StatusBadge status={form.status} />
                <View style={styles.metaBadge}>
                  <AppText variant="caption" color={colors.onSurfaceTertiary}>{form.condition}</AppText>
                </View>
                <View style={styles.metaBadge}>
                  <AppText variant="caption" color={colors.onSurfaceTertiary}>{form.genre}</AppText>
                </View>
                <View style={styles.metaBadge}>
                  <AppText variant="caption" color={colors.onSurfaceTertiary}>{form.language}</AppText>
                </View>
              </View>
            </View>

            <Pressable testID="owner-card" onPress={() => router.push(`/person/${data.owner.user_id}`)} style={styles.ownerCard}>
              <Avatar uri={data.owner.avatar_url} name={data.owner.name} size={48} />
              <View style={{ flex: 1, gap: 3 }}>
                <AppText variant="heading">{data.owner.name}</AppText>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
                    <MapPin size={13} color={colors.brandSecondary} weight="fill" />
                    <AppText variant="caption" color={colors.muted}>
                      {data.owner.distance_km < 999 ? `${data.owner.distance_km} km` : data.owner.neighborhood}
                    </AppText>
                  </View>
                  <RatingPill rating={data.owner.rating} count={data.owner.rating_count} />
                </View>
              </View>
            </Pressable>
          </ScrollView>
          <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>
            <Button
              testID="book-request-swap"
              title={`Request swap with ${data.owner.name?.split(" ")[0]}`}
              icon={<ArrowsClockwise size={18} color={colors.onBrandPrimary} weight="bold" />}
              onPress={startSwap}
            />
          </View>
        </>
      )}
    </View>
  );
}

function EditGroup({ label, options, value, onChange }: { label: string; options: string[]; value: string; onChange: (v: string) => void }) {
  const styles = useStyles();
  return (
    <View style={{ gap: 10 }}>
      <AppText variant="label">{label}</AppText>
      <View style={styles.chipWrap}>
        {options.map((o) => (
          <Chip key={o} label={o} selected={value === o} onPress={() => onChange(o)} testID={`edit-${label}-${o}`} />
        ))}
      </View>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: colors.border },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  badgeRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, justifyContent: "center", marginTop: 4 },
  metaBadge: { backgroundColor: colors.surfaceTertiary, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  wantedPill: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.brandTertiary, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999, maxWidth: "100%" },
  ownerCard: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.surfaceSecondary, borderRadius: 18, padding: 14, borderWidth: 1, borderColor: colors.border, marginTop: 24 },
  footer: { position: "absolute", bottom: 0, left: 0, right: 0, paddingHorizontal: 20, paddingTop: 12, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border },
}));
