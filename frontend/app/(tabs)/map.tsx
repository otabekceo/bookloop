import { useRef, useState, useCallback } from "react";
import { View, ActivityIndicator } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import BottomSheet, { BottomSheetBackdrop, BottomSheetView } from "@gorhom/bottom-sheet";
import { MapPin, Users, BookOpen, ArrowRight } from "phosphor-react-native";

import { AppText, Button } from "@/src/components/ui";
import DensityMap from "@/src/components/DensityMap";
import { apiFetch } from "@/src/api";
import { makeStyles, useTheme } from "@/src/theme";

type Cluster = {
  neighborhood: string;
  lat: number;
  lng: number;
  people_count: number;
  books_count: number;
  top_genres: string[];
};

export default function MapScreen() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const sheetRef = useRef<BottomSheet>(null);
  const [selected, setSelected] = useState<Cluster | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["clusters"],
    queryFn: () =>
      apiFetch<{ clusters: Cluster[]; total_active: number; total_books: number }>("/api/map/clusters"),
  });

  const clusters = data?.clusters || [];

  const onSelect = useCallback(
    (nb: string) => {
      const c = clusters.find((x) => x.neighborhood === nb);
      if (c) {
        setSelected(c);
        sheetRef.current?.expand();
      }
    },
    [clusters],
  );

  const renderBackdrop = useCallback(
    (props: any) => <BottomSheetBackdrop {...props} disappearsOnIndex={-1} appearsOnIndex={0} opacity={0.4} />,
    [],
  );

  return (
    <View style={styles.root}>
      <View style={styles.mapWrap}>
        {isLoading ? (
          <View style={styles.loading}>
            <ActivityIndicator color={colors.brandPrimary} />
          </View>
        ) : (
          <DensityMap clusters={clusters} onSelect={onSelect} />
        )}
        {/* Floating header */}
        <View style={[styles.header, { paddingTop: insets.top + 10 }]} pointerEvents="none">
          <View style={styles.headerPill}>
            <MapPin size={16} color={colors.brandSecondary} weight="fill" />
            <AppText variant="heading">Messina</AppText>
          </View>
          <View style={styles.headerPillSmall}>
            <AppText variant="caption" color={colors.muted}>Active exchanger density</AppText>
          </View>
        </View>
      </View>

      <View style={[styles.metricCard, { marginBottom: insets.bottom + 16 }]}>
        <View style={styles.metricIcon}>
          <Users size={22} color={colors.onBrandPrimary} weight="fill" />
        </View>
        <View style={{ flex: 1 }}>
          <AppText variant="title">{data?.total_active ?? 0} readers nearby</AppText>
          <AppText variant="body" color={colors.muted}>
            currently willing to exchange {data?.total_books ?? 0} books · tap a cluster
          </AppText>
        </View>
      </View>

      <BottomSheet
        ref={sheetRef}
        index={-1}
        snapPoints={[400]}
        enablePanDownToClose
        backdropComponent={renderBackdrop}
        backgroundStyle={{ backgroundColor: colors.surfaceSecondary }}
        handleIndicatorStyle={{ backgroundColor: colors.borderStrong }}
      >
        <BottomSheetView style={{ padding: 22, paddingBottom: insets.bottom + 20, gap: 16 }}>
          {selected && (
            <>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <MapPin size={18} color={colors.brandSecondary} weight="fill" />
                <AppText variant="title">{selected.neighborhood}</AppText>
              </View>
              <View style={{ flexDirection: "row", gap: 12 }}>
                <View style={styles.statBox}>
                  <Users size={20} color={colors.brandPrimary} weight="fill" />
                  <AppText variant="title">{selected.people_count}</AppText>
                  <AppText variant="caption" color={colors.muted}>active exchangers</AppText>
                </View>
                <View style={styles.statBox}>
                  <BookOpen size={20} color={colors.brandSecondary} weight="fill" />
                  <AppText variant="title">{selected.books_count}</AppText>
                  <AppText variant="caption" color={colors.muted}>books available</AppText>
                </View>
              </View>
              {selected.top_genres.length > 0 && (
                <View style={{ gap: 8 }}>
                  <AppText variant="label" color={colors.muted}>Popular genres</AppText>
                  <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                    {selected.top_genres.map((g) => (
                      <View key={g} style={styles.genreTag}>
                        <AppText variant="caption" color={colors.onBrandTertiary}>{g}</AppText>
                      </View>
                    ))}
                  </View>
                </View>
              )}
              <Button
                testID="explore-people-button"
                title="Explore people"
                icon={<ArrowRight size={18} color={colors.onBrandPrimary} weight="bold" />}
                onPress={() => {
                  sheetRef.current?.close();
                  router.push("/(tabs)/discover");
                }}
              />
            </>
          )}
        </BottomSheetView>
      </BottomSheet>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  mapWrap: { flex: 1, overflow: "hidden" },
  loading: { flex: 1, alignItems: "center", justifyContent: "center" },
  header: { position: "absolute", top: 0, left: 0, right: 0, paddingHorizontal: 16, flexDirection: "row", alignItems: "center", gap: 8 },
  headerPill: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.surfaceSecondary, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, borderWidth: 1, borderColor: colors.border },
  headerPillSmall: { backgroundColor: colors.surfaceSecondary, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, borderWidth: 1, borderColor: colors.border },
  metricCard: { flexDirection: "row", alignItems: "center", gap: 14, marginHorizontal: 20, marginTop: 12, backgroundColor: colors.surfaceSecondary, borderRadius: 20, padding: 18, borderWidth: 1, borderColor: colors.border },
  metricIcon: { width: 46, height: 46, borderRadius: 23, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  statBox: { flex: 1, backgroundColor: colors.surfaceTertiary, borderRadius: 16, padding: 16, gap: 4, alignItems: "flex-start" },
  genreTag: { backgroundColor: colors.brandTertiary, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999 },
}));
