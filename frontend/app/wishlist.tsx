import { useEffect, useState } from "react";
import { View, ScrollView, Pressable, ActivityIndicator, useWindowDimensions } from "react-native";
import { useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Sparkle, MapPin, CaretDown, CaretUp } from "phosphor-react-native";

import { AppText, Button, Chip, Avatar, RatingPill, DirectionalIcon, haptic, useToast } from "@/src/components/ui";
import { BookTile, Book, Person } from "@/src/components/cards";
import { useAuth } from "@/src/auth";
import { apiFetch } from "@/src/api";
import { GENRES, LANGUAGES, READING_INTERESTS } from "@/src/constants";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";

type WishBook = Book & { owner_avatar?: string | null; owner_exchanging?: boolean; distance_km: number; match_score: number };
type WishData = {
  preferences: { genres: string[]; languages: string[]; reading_interests: string[] };
  books: WishBook[];
  people: Person[];
  needs_setup: boolean;
};

export default function Wishlist() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const { t } = useLanguage();
  const { user, refreshUser } = useAuth();
  const { width } = useWindowDimensions();
  const tile = (width - 40 - 24) / 2.4;

  const [genres, setGenres] = useState<string[]>(user?.genres || []);
  const [languages, setLanguages] = useState<string[]>(user?.languages || []);
  const [interests, setInterests] = useState<string[]>(user?.reading_interests || []);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState((user?.genres?.length || 0) === 0);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["wishlist"],
    queryFn: () => apiFetch<WishData>("/api/wishlist"),
  });

  useEffect(() => {
    if (data?.needs_setup) setEditing(true);
  }, [data?.needs_setup]);

  const same = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));
  const dirty =
    !same(genres, user?.genres || []) || !same(languages, user?.languages || []) || !same(interests, user?.reading_interests || []);

  const toggle = (arr: string[], set: (v: string[]) => void, v: string) => {
    haptic("selection");
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  };

  const save = async () => {
    if (genres.length === 0) {
      toast(t("wishlist.pickAtLeastOneGenre"), "error");
      return;
    }
    setSaving(true);
    try {
      await apiFetch("/api/users/me", { method: "PUT", body: { genres, languages, reading_interests: interests } });
      await refreshUser();
      await refetch();
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["bookDemand"] });
      haptic("success");
      toast(t("wishlist.wishlistUpdated"), "success");
      setEditing(false);
    } catch (e: any) {
      toast(e.message || t("wishlist.couldNotSave"), "error");
    } finally {
      setSaving(false);
    }
  };

  const books = data?.books || [];
  const people = data?.people || [];

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.iconBtn}>
          <DirectionalIcon>
            <ArrowLeft size={22} color={colors.onSurface} />
          </DirectionalIcon>
        </Pressable>
        <AppText variant="heading">{t("wishlist.title")}</AppText>
        <View style={{ width: 42 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: insets.bottom + 40, gap: 20 }} showsVerticalScrollIndicator={false}>
        <View>
          <AppText variant="display">{t("wishlist.heading")}</AppText>
          <AppText variant="body" color={colors.muted} style={{ marginTop: 6 }}>
            {t("wishlist.subtitle")}
          </AppText>
        </View>

        {/* Preferences */}
        <View style={styles.card}>
          <Pressable testID="toggle-preferences" onPress={() => setEditing((v) => !v)} style={styles.cardHead}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <Sparkle size={18} color={colors.brandPrimary} weight="fill" />
              <AppText variant="heading">{t("wishlist.yourPreferences")}</AppText>
            </View>
            {editing ? <CaretUp size={18} color={colors.muted} weight="bold" /> : <CaretDown size={18} color={colors.muted} weight="bold" />}
          </Pressable>

          {!editing ? (
            <View style={styles.tagWrap}>
              {genres.map((g) => (
                <View key={g} style={styles.tag}>
                  <AppText variant="caption" color={colors.onBrandTertiary}>{g}</AppText>
                </View>
              ))}
              {languages.map((l) => (
                <View key={l} style={[styles.tag, { backgroundColor: colors.sageSoft }]}>
                  <AppText variant="caption" color={colors.brandSecondary}>{l}</AppText>
                </View>
              ))}
              {interests.map((i) => (
                <View key={i} style={[styles.tag, { backgroundColor: colors.surfaceTertiary }]}>
                  <AppText variant="caption" color={colors.onSurfaceTertiary}>{i}</AppText>
                </View>
              ))}
            </View>
          ) : (
            <View style={{ gap: 16 }}>
              <PrefGroup label={t("wishlist.genresYouLove")} options={GENRES} value={genres} onToggle={(v) => toggle(genres, setGenres, v)} idPrefix="wish-genre" />
              <PrefGroup label={t("wishlist.languages")} options={LANGUAGES} value={languages} onToggle={(v) => toggle(languages, setLanguages, v)} idPrefix="wish-lang" />
              <PrefGroup label={t("wishlist.readingInterests")} options={READING_INTERESTS} value={interests} onToggle={(v) => toggle(interests, setInterests, v)} idPrefix="wish-interest" />
              <Button testID="save-wishlist" title={t("wishlist.saveWishlist")} onPress={save} loading={saving} disabled={!dirty && (user?.genres?.length || 0) > 0} />
            </View>
          )}
        </View>

        {isLoading ? (
          <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 20 }} />
        ) : data?.needs_setup ? (
          <AppText variant="body" color={colors.muted} style={{ textAlign: "center" }}>
            {t("wishlist.pickGenresHint")}
          </AppText>
        ) : (
          <>
            {/* Books for you */}
            <View style={{ gap: 10 }}>
              <AppText variant="heading">{t("wishlist.booksForYou", { count: books.length })}</AppText>
              {books.length > 0 ? (
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 14, paddingRight: 20 }}>
                  {books.map((b) => (
                    <View key={b.id} style={{ width: tile, gap: 4 }}>
                      <BookTile book={b} width={tile} showOwner onPress={() => router.push(`/book/${b.id}`)} />
                      <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
                        <MapPin size={11} color={colors.brandSecondary} weight="fill" />
                        <AppText variant="caption" color={colors.muted} style={{ fontSize: 11 }}>
                          {b.distance_km < 999 ? `${b.distance_km} km` : t("common.nearby")} · {b.genre}
                        </AppText>
                      </View>
                    </View>
                  ))}
                </ScrollView>
              ) : (
                <AppText variant="body" color={colors.muted}>
                  {t("wishlist.noMatchingBooks")}
                </AppText>
              )}
            </View>

            {/* Readers for you */}
            <View style={{ gap: 10 }}>
              <AppText variant="heading">{t("wishlist.readersForYou", { count: people.length })}</AppText>
              {people.length > 0 ? (
                people.map((p) => (
                  <Pressable
                    key={p.user_id}
                    testID={`wish-person-${p.user_id}`}
                    onPress={() => {
                      haptic("light");
                      router.push(`/person/${p.user_id}`);
                    }}
                    style={styles.personRow}
                  >
                    <Avatar uri={p.avatar_url} name={p.name} size={46} />
                    <View style={{ flex: 1, gap: 3 }}>
                      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        <AppText variant="label">{p.name}</AppText>
                        <RatingPill rating={p.rating} count={p.rating_count} />
                      </View>
                      <AppText variant="caption" color={colors.brandPrimary} numberOfLines={1}>
                        {[...(p.shared_genres || []), ...(p.shared_interests || [])].slice(0, 3).join(" · ") || t("wishlist.sharesYourLanguages")}
                      </AppText>
                      <AppText variant="caption" color={colors.muted}>
                        {p.distance_km < 999 ? `${p.distance_km} km` : p.neighborhood} · {t("wishlist.booksForYouShort", { count: p.available_count || 0 })}
                        {p.is_exchanging ? ` ${t("wishlist.exchangingNow")}` : ""}
                      </AppText>
                    </View>
                  </Pressable>
                ))
              ) : (
                <AppText variant="body" color={colors.muted}>
                  {t("wishlist.noMatchingReaders")}
                </AppText>
              )}
            </View>
          </>
        )}
      </ScrollView>
    </View>
  );
}

function PrefGroup({ label, options, value, onToggle, idPrefix }: { label: string; options: string[]; value: string[]; onToggle: (v: string) => void; idPrefix: string }) {
  const styles = useStyles();
  return (
    <View style={{ gap: 10 }}>
      <AppText variant="label">{label}</AppText>
      <View style={styles.tagWrap}>
        {options.map((o) => (
          <Chip key={o} label={o} selected={value.includes(o)} onPress={() => onToggle(o)} testID={`${idPrefix}-${o}`} />
        ))}
      </View>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  card: { backgroundColor: colors.surfaceSecondary, borderRadius: 20, padding: 16, borderWidth: 1, borderColor: colors.border, gap: 14 },
  cardHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  tagWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  tag: { backgroundColor: colors.brandTertiary, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999 },
  personRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.surfaceSecondary, borderRadius: 16, padding: 12, borderWidth: 1, borderColor: colors.border },
}));
