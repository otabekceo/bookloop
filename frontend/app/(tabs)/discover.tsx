import { useRef, useState, useMemo, useCallback } from "react";
import { View, FlatList, useWindowDimensions, Pressable, RefreshControl, ActivityIndicator } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import BottomSheet, { BottomSheetBackdrop, BottomSheetView } from "@gorhom/bottom-sheet";
import { MagnifyingGlass, SlidersHorizontal, Bell, CaretDown, BookOpen } from "phosphor-react-native";

import { AppText, Chip, ChipRow, Field, Button, EmptyState, haptic } from "@/src/components/ui";
import { PersonCard, BookTile, Person, Book } from "@/src/components/cards";
import { apiFetch } from "@/src/api";
import { GENRES, LANGUAGES, DISTANCES } from "@/src/constants";
import { makeStyles, useTheme } from "@/src/theme";
import { FONTS } from "@/src/typography";

export default function Discover() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
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
      apiFetch<{ people: Person[] }>(
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

  const Header = useMemo(
    () => (
      <View style={[styles.header, { paddingTop: insets.top + 10 }]}>
        <View style={styles.topRow}>
          <AppText style={styles.wordmark}>
            Book<AppText style={[styles.wordmark, { color: colors.brandSecondary }]}>Loop</AppText>
          </AppText>
          <Pressable testID="notif-bell" onPress={() => router.push("/(tabs)/swaps")} style={styles.bell}>
            <Bell size={22} color={colors.onSurface} weight="regular" />
            {(notif?.total || 0) > 0 && <View style={styles.bellDot} />}
          </Pressable>
        </View>

        <AppText variant="display" style={{ marginTop: 6, marginBottom: 12 }}>
          Discover readers{"\n"}near you.
        </AppText>

        <View style={styles.searchRow}>
          <View style={styles.searchPill}>
            <MagnifyingGlass size={18} color={colors.muted} />
            <Field
              testID="search-input"
              placeholder="Search people, books or authors"
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
          {(["people", "books"] as const).map((t) => (
            <Pressable
              key={t}
              testID={`segment-${t}`}
              onPress={() => {
                haptic("selection");
                setTab(t);
              }}
              style={[styles.segItem, tab === t && styles.segItemActive]}
            >
              <AppText variant="label" color={tab === t ? colors.onSurfaceInverse : colors.muted}>
                {t === "people" ? "People" : "Books"}
              </AppText>
            </Pressable>
          ))}
        </View>
      </View>
    ),
    [insets.top, search, tab, notif, colors, styles, openFilters, router],
  );

  const ChipsBar = (
    <ChipRow style={{ marginBottom: 4 }}>
      <Chip label="All" selected={genre === "All"} onPress={() => setGenre("All")} testID="genre-chip-All" />
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
          contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 14, paddingBottom: 24 }}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={peopleQ.isRefetching} onRefresh={peopleQ.refetch} tintColor={colors.brandPrimary} />}
          ListEmptyComponent={
            <EmptyState
              icon={<BookOpen size={48} color={colors.muted} weight="light" />}
              title="No readers found"
              subtitle="Try widening your distance or clearing filters."
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
              title="No books found"
              subtitle="Try a different search or genre."
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
          <AppText variant="title">Filters</AppText>

          <View style={{ gap: 10 }}>
            <AppText variant="label">Distance</AppText>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
              {DISTANCES.map((d) => (
                <Chip key={d} label={`${d} km`} selected={maxDistance === d} onPress={() => setMaxDistance(d)} testID={`distance-${d}`} />
              ))}
            </View>
          </View>

          <View style={{ gap: 10 }}>
            <AppText variant="label">Language</AppText>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
              <Chip label="All" selected={language === "All"} onPress={() => setLanguage("All")} />
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
                <AppText variant="heading">Currently exchanging only</AppText>
                <AppText variant="caption" color={colors.muted}>
                  Show people ready to swap right now
                </AppText>
              </View>
              <View style={[styles.switch, exchangingOnly && styles.switchOn]}>
                <View style={[styles.knob, exchangingOnly && styles.knobOn]} />
              </View>
            </Pressable>
          )}

          <Button title="Show results" onPress={() => sheetRef.current?.close()} testID="apply-filters" />
        </BottomSheetView>
      </BottomSheet>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: 20, backgroundColor: colors.surface },
  topRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  wordmark: { fontFamily: FONTS.display, fontSize: 26, lineHeight: 30, color: colors.onSurface },
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
}));
