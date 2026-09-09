import { useRef, useMemo, useState, useCallback } from "react";
import { View, Pressable, ActivityIndicator, useWindowDimensions } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { LinearGradient } from "expo-linear-gradient";
import BottomSheet, { BottomSheetBackdrop, BottomSheetView } from "@gorhom/bottom-sheet";
import { MapPin, Users, BookOpen, ArrowRight } from "phosphor-react-native";

import { AppText, Button, haptic } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { makeStyles, useTheme } from "@/src/theme";
import { FONTS } from "@/src/typography";

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
  const { width, height } = useWindowDimensions();
  const sheetRef = useRef<BottomSheet>(null);
  const [selected, setSelected] = useState<Cluster | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["clusters"],
    queryFn: () =>
      apiFetch<{ clusters: Cluster[]; total_active: number; total_books: number }>("/api/map/clusters"),
  });

  const clusters = data?.clusters || [];
  const mapH = height - insets.top - 320;

  const positioned = useMemo(() => {
    if (clusters.length === 0) return [];
    const lats = clusters.map((c) => c.lat);
    const lngs = clusters.map((c) => c.lng);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
    const padX = 70, padY = 60;
    const w = width - padX * 2;
    const h = Math.max(mapH - padY * 2, 180);
    return clusters.map((c) => {
      const nx = maxLng === minLng ? 0.5 : (c.lng - minLng) / (maxLng - minLng);
      const ny = maxLat === minLat ? 0.5 : (c.lat - minLat) / (maxLat - minLat);
      return { ...c, x: padX + nx * w, y: padY + (1 - ny) * h };
    });
  }, [clusters, width, mapH]);

  const open = useCallback((c: Cluster) => {
    haptic("light");
    setSelected(c);
    sheetRef.current?.expand();
  }, []);

  const renderBackdrop = useCallback(
    (props: any) => <BottomSheetBackdrop {...props} disappearsOnIndex={-1} appearsOnIndex={0} opacity={0.4} />,
    [],
  );

  return (
    <View style={styles.root}>
      <View style={[styles.mapArea, { height: mapH + insets.top + 20, paddingTop: insets.top + 10 }]}>
        <LinearGradient colors={[colors.sageSoft, colors.surfaceTertiary]} style={styles.mapBg} />
        {/* faux map grid */}
        {[0.25, 0.5, 0.75].map((p) => (
          <View key={`h${p}`} style={[styles.gridLine, { top: (mapH + insets.top) * p, width, height: 1 }]} />
        ))}
        {[0.25, 0.5, 0.75].map((p) => (
          <View key={`v${p}`} style={[styles.gridLine, { left: width * p, width: 1, height: mapH + insets.top }]} />
        ))}

        <View style={styles.mapHeader}>
          <AppText variant="title" color={colors.onSurface}>
            Messina
          </AppText>
          <AppText variant="caption" color={colors.muted}>
            Book exchange density map
          </AppText>
        </View>

        {isLoading ? (
          <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 120 }} />
        ) : (
          positioned.map((c) => {
            const size = 44 + Math.min(c.people_count, 6) * 4;
            return (
              <Pressable
                key={c.neighborhood}
                testID={`cluster-${c.neighborhood}`}
                onPress={() => open(c)}
                style={[styles.markerWrap, { left: c.x - size / 2, top: c.y - size / 2 }]}
              >
                <View style={[styles.marker, { width: size, height: size, borderRadius: size / 2 }]}>
                  <AppText variant="heading" color={colors.onBrandSecondary}>
                    {c.people_count}
                  </AppText>
                </View>
                <View style={styles.markerLabel}>
                  <AppText variant="caption" color={colors.onSurface} style={{ fontFamily: FONTS.medium }}>
                    {c.neighborhood}
                  </AppText>
                </View>
              </Pressable>
            );
          })
        )}
      </View>

      <View style={[styles.metricCard, { marginBottom: insets.bottom + 16 }]}>
        <View style={styles.metricIcon}>
          <Users size={22} color={colors.onBrandPrimary} weight="fill" />
        </View>
        <View style={{ flex: 1 }}>
          <AppText variant="title">
            {data?.total_active ?? 0} readers nearby
          </AppText>
          <AppText variant="body" color={colors.muted}>
            are currently willing to exchange {data?.total_books ?? 0} books
          </AppText>
        </View>
      </View>

      <BottomSheet
        ref={sheetRef}
        index={-1}
        snapPoints={[380]}
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
                  <AppText variant="caption" color={colors.muted}>
                    exchanging
                  </AppText>
                </View>
                <View style={styles.statBox}>
                  <BookOpen size={20} color={colors.brandSecondary} weight="fill" />
                  <AppText variant="title">{selected.books_count}</AppText>
                  <AppText variant="caption" color={colors.muted}>
                    available
                  </AppText>
                </View>
              </View>
              {selected.top_genres.length > 0 && (
                <View style={{ gap: 8 }}>
                  <AppText variant="label" color={colors.muted}>
                    Popular genres
                  </AppText>
                  <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                    {selected.top_genres.map((g) => (
                      <View key={g} style={styles.genreTag}>
                        <AppText variant="caption" color={colors.onBrandTertiary}>
                          {g}
                        </AppText>
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
  mapArea: { width: "100%", overflow: "hidden" },
  mapBg: { ...StyleSheetAbsolute() },
  gridLine: { position: "absolute", backgroundColor: colors.border, opacity: 0.5 },
  mapHeader: { paddingHorizontal: 20, gap: 2 },
  markerWrap: { position: "absolute", alignItems: "center" },
  marker: {
    backgroundColor: colors.brandSecondary,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 3,
    borderColor: colors.surfaceSecondary,
    shadowColor: "#000",
    shadowOpacity: 0.15,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 3 },
    elevation: 4,
  },
  markerLabel: { marginTop: 4, backgroundColor: colors.surfaceSecondary, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8, borderWidth: 1, borderColor: colors.border },
  metricCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    marginHorizontal: 20,
    marginTop: 16,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: 20,
    padding: 18,
    borderWidth: 1,
    borderColor: colors.border,
  },
  metricIcon: { width: 46, height: 46, borderRadius: 23, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  statBox: { flex: 1, backgroundColor: colors.surfaceTertiary, borderRadius: 16, padding: 16, gap: 4, alignItems: "flex-start" },
  genreTag: { backgroundColor: colors.brandTertiary, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999 },
}));

function StyleSheetAbsolute() {
  return { position: "absolute" as const, top: 0, left: 0, right: 0, bottom: 0 };
}
