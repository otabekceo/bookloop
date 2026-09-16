import { useRef, useState, useMemo, useCallback } from "react";
import { View, FlatList, ScrollView, useWindowDimensions, Pressable, RefreshControl, ActivityIndicator } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import BottomSheet, { BottomSheetBackdrop, BottomSheetView } from "@gorhom/bottom-sheet";
import { MagnifyingGlass, SlidersHorizontal, Bell, BookOpen, Sparkle, ArrowRight } from "phosphor-react-native";

import { AppText, Chip, ChipRow, Field, Button, EmptyState, DirectionalIcon, haptic } from "@/src/components/ui";
import { Logo } from "@/src/components/Logo";
import { PersonCard, MatchCard, BookTile, Person, Book } from "@/src/components/cards";
import { apiFetch } from "@/src/api";
import { useAuth } from "@/src/auth";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { GENRES, LANGUAGES, DISTANCES } from "@/src/constants";
import { makeStyles, useTheme } from "@/src/theme";
import { fontsForLanguage } from "@/src/typography";

export default function Discover() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const { t, language: uiLanguage } = useLanguage();
  const uiFonts = fontsForLanguage(uiLanguage);
  const { width } = useWindowDimensions();
  const col = (width - 40 - 14) / 2;

  const [tab, setTab] = useState<"people" | "books">("people");
  const [search, setSearch] = useState("");
  const [genre, setGenre] = useState("All");
  const [language, setLanguage] = useState("All");
  const [maxDistance, setMaxDistance] = useState(25);
  const [exchangingOnly, setExchangingOnly] = useState(true);

  const sheetRef = useRef<BottomSheet>(null);

  const { data: notif } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => apiFetch<{ total: number }>("/api/notifications"),
  });

  const peopleQ = useQuery({
    queryKey: ["people", search, genre, language, maxDistance, exchangingOnly],
    enabled: tab === "people",
    queryFn: () =>
      apiFetch<{ people: Person[]; top_matches: Person[] }>(
        `/api/discover/people?search=${encodeURIComponent(search)}&genre=${genre}&language=${language}&max_distance=${maxDistance}&exchanging=${exchangingOnly}`,
      ),
  });

  const booksQ = useQuery({
    queryKey: ["discoverBooks", search, genre, language],
    enabled: tab === "books",
    queryFn: () =>
      apiFetch<{ books: Book[] }>(
        `/api/discover/books?search=${encodeURIComponent(search)}&genre=${genre}&language=${language}`,
      ),
  });

  const openFilters = useCallback(() => {
    haptic("light");
    sheetRef.current?.expand();
  }, []);

  const renderBackdrop = useCallback(
    (props: any) => <BottomSheetBackdrop {...props} disappearsOnIndex={-1} appearsOnIndex={0} opacity={0.4} />,
    [],
  );

  const loading = tab === "people" ? peopleQ.isLoading : booksQ.isLoading;
  const people = peopleQ.data?.people || [];
  const books = booksQ.data?.books || [];
  const topMatches = peopleQ.data?.top_matches || [];
  const showMatches = !search && genre === "All";
  const hasGenres = (user?.genres?.length || 0) > 0;

  const MatchesStrip = showMatches ? (
    hasGenres && topMatches.length > 0 ? (
      <View style={styles.matches} testID="genre-matches">
        <View style={styles.matchesHead}>
          <Sparkle size={16} color={colors.brandPrimary} weight="fill" />
          <AppText variant="heading">{t("discover.greatMatches")}</AppText>
        </View>
        <AppText variant="caption" color={colors.muted}>
          {t("discover.greatMatchesSubtitle")}
        </AppText>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }} contentContainerStyle={{ gap: 10, paddingTop: 10 }}>
          {topMatches.map((p) => (
            <MatchCard key={p.user_id} person={p} />
          ))}
        </ScrollView>
        <AppText variant="heading" style={{ marginTop: 18 }}>
          {t("discover.allReadersNearby")}
        </AppText>
      </View>
    ) : !hasGenres ? (
      <Pressable testID="matches-setup" onPress={() => router.push("/wishlist")} style={styles.setupCard}>
        <Sparkle size={20} color={colors.brandPrimary} weight="fill" />
        <View style={{ flex: 1 }}>
          <AppText variant="label">{t("discover.seeBestMatches")}</AppText>
          <AppText variant="caption" color={colors.muted}>
            {t("discover.setupMatches")}
          </AppText>
        </View>
        <DirectionalIcon>
          <ArrowRight size={16} color={colors.brandPrimary} weight="bold" />
        </DirectionalIcon>
      </Pressable>
    ) : null
  ) : null;

  const Header = useMemo(
    () => (
      <View style={[styles.header, { paddingTop: insets.top + 10 }]}>
        <View style={styles.topRow}>
          <Logo variant="wordmark" height={24} />
          <Pressable testID="notif-bell" onPress={() => router.push("/(tabs)/swaps")} style={styles.bell}>
            <Bell size={22} color={colors.onSurface} weight="regular" />
            {(notif?.total || 0) > 0 && <View style={styles.bellDot} />}
          </Pressable>
        </View>

        <AppText variant="display" style={{ marginTop: 6, marginBottom: 12 }}>
          {t("discover.title")}
        </AppText>

        <View style={styles.searchRow}>
          <View style={styles.searchPill}>
            <MagnifyingGlass size={18} color={colors.muted} />
            <Field
              testID="search-input"
              placeholder={t("discover.searchPlaceholder")}
              value={search}
              onChangeText={setSearch}
              style={styles.searchInput}
            />
          </View>
          <Pressable testID="filter-button" onPress={openFilters} style={styles.filterBtn}>
            <SlidersHorizontal size={20} color={colors.onBrandPrimary} />
          </Pressable>
        </View>

        <View style={styles.segment}>
          {(["people", "books"] as const).map((segKey) => (
            <Pressable
              key={segKey}
              testID={`segment-${segKey}`}
              onPress={() => {
                haptic("selection");
                setTab(segKey);
              }}
              style={[styles.segItem, tab === segKey && styles.segItemActive]}
            >
              <AppText variant="label" color={tab === segKey ? colors.onSurfaceInverse : colors.muted}>
                {segKey === "people" ? t("discover.people") : t("discover.books")}
              </AppText>
            </Pressable>
          ))}
        </View>
      </View>
    ),
    [insets.top, search, tab, notif, colors, styles, openFilters, router, t],
  );

  const ChipsBar = (
    <ChipRow style={{ marginBottom: 4 }}>
      <Chip label={t("common.all")} selected={genre === "All"} onPress={() => setGenre("All")} testID="genre-chip-All" />
      {GENRES.map((g) => (
        <Chip key={g} label={g} selected={genre === g} onPress={() => setGenre(g)} testID={`genre-chip-${g}`} />
      ))}
    </ChipRow>
  );

  return (
    <View style={styles.root}>
      {Header}
      {ChipsBar}
      {loading ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 40 }} />
      ) : tab === "people" ? (
        <FlatList
          data={people}
          keyExtractor={(p) => p.user_id}
          renderItem={({ item }) => <PersonCard person={item} />}
          ListHeaderComponent={MatchesStrip}
          contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 14, paddingBottom: 24 }}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={peopleQ.isRefetching} onRefresh={peopleQ.refetch} tintColor={colors.brandPrimary} />}
          ListEmptyComponent={
            <EmptyState
              icon={<BookOpen size={48} color={colors.muted} weight="light" />}
              title={t("discover.noReadersTitle")}
              subtitle={t("discover.noReadersBody")}
            />
          }
        />
      ) : (
        <FlatList
          data={books}
          keyExtractor={(b) => b.id}
          numColumns={2}
          columnWrapperStyle={{ gap: 14 }}
          renderItem={({ item }) => (
            <BookTile book={item} width={col} showOwner onPress={() => router.push(`/person/${item.owner_id}`)} />
          )}
          contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 18, paddingBottom: 24 }}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={booksQ.isRefetching} onRefresh={booksQ.refetch} tintColor={colors.brandPrimary} />}
          ListEmptyComponent={
            <EmptyState
              icon={<BookOpen size={48} color={colors.muted} weight="light" />}
              title={t("discover.noBooksTitle")}
              subtitle={t("discover.noBooksBody")}
            />
          }
        />
      )}

      <BottomSheet
        ref={sheetRef}
        index={-1}
        snapPoints={[520]}
        enablePanDownToClose
        backdropComponent={renderBackdrop}
        backgroundStyle={{ backgroundColor: colors.surfaceSecondary }}
        handleIndicatorStyle={{ backgroundColor: colors.borderStrong }}
      >
        <BottomSheetView style={{ padding: 20, paddingBottom: insets.bottom + 20, gap: 18 }}>
          <AppText variant="title">{t("common.filters")}</AppText>

          <View style={{ gap: 10 }}>
            <AppText variant="label">{t("common.distance")}</AppText>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
              {DISTANCES.map((d) => (
                <Chip key={d} label={`${d} km`} selected={maxDistance === d} onPress={() => setMaxDistance(d)} testID={`distance-${d}`} />
              ))}
            </View>
          </View>

          <View style={{ gap: 10 }}>
            <AppText variant="label">{t("common.language")}</AppText>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
              <Chip label={t("common.all")} selected={language === "All"} onPress={() => setLanguage("All")} />
              {LANGUAGES.map((l) => (
                <Chip key={l} label={l} selected={language === l} onPress={() => setLanguage(l)} testID={`lang-${l}`} />
              ))}
            </View>
          </View>

          {tab === "people" && (
            <Pressable
              testID="exchanging-toggle"
              onPress={() => {
                haptic("selection");
                setExchangingOnly((v) => !v);
              }}
              style={styles.toggleRow}
            >
              <View>
                <AppText variant="heading">{t("discover.exchangingOnly")}</AppText>
                <AppText variant="caption" color={colors.muted}>
                  {t("discover.exchangingOnlyHint")}
                </AppText>
              </View>
              <View style={[styles.switch, exchangingOnly && styles.switchOn]}>
                <View style={[styles.knob, exchangingOnly && styles.knobOn]} />
              </View>
            </Pressable>
          )}

          <Button title={t("discover.showResults")} onPress={() => sheetRef.current?.close()} testID="apply-filters" />
        </BottomSheetView>
      </BottomSheet>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: 20, backgroundColor: colors.surface },
  topRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  bell: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  bellDot: { position: "absolute", top: 10, right: 12, width: 9, height: 9, borderRadius: 5, backgroundColor: colors.brandPrimary },
  searchRow: { flexDirection: "row", gap: 10, alignItems: "center" },
  searchPill: { flex: 1, flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surfaceSecondary, borderRadius: 14, borderWidth: 1, borderColor: colors.border, paddingLeft: 14 },
  searchInput: { flex: 1, borderWidth: 0, backgroundColor: "transparent", paddingLeft: 0, paddingVertical: 12 },
  filterBtn: { width: 50, height: 50, borderRadius: 14, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  segment: { flexDirection: "row", backgroundColor: colors.surfaceTertiary, borderRadius: 12, padding: 4, marginTop: 14 },
  segItem: { flex: 1, alignItems: "center", justifyContent: "center", paddingVertical: 9, borderRadius: 9 },
  segItemActive: { backgroundColor: colors.surfaceInverse },
  toggleRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  switch: { width: 50, height: 30, borderRadius: 15, backgroundColor: colors.surfaceTertiary, padding: 3, justifyContent: "center" },
  switchOn: { backgroundColor: colors.brandSecondary },
  knob: { width: 24, height: 24, borderRadius: 12, backgroundColor: "#FFFFFF" },
  knobOn: { alignSelf: "flex-end" },
  matches: { marginBottom: 4 },
  matchesHead: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 2 },
  setupCard: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.brandTertiary, borderRadius: 16, padding: 14, marginBottom: 4 },
}));
