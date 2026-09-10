import { useState } from "react";
import { View, ScrollView, Pressable, ActivityIndicator, useWindowDimensions } from "react-native";
import { useLocalSearchParams, useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, MapPin, ArrowsClockwise, ArrowRight } from "phosphor-react-native";

import { AppText, Avatar, Button, Stars, RatingPill, ExchangingDot, EmptyState, haptic, useToast } from "@/src/components/ui";
import { BookTile, Book } from "@/src/components/cards";
import { apiFetch } from "@/src/api";
import { makeStyles, useTheme } from "@/src/theme";

export default function PersonProfile() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const { width } = useWindowDimensions();
  const col = (width - 40 - 14) / 2;
  const [requesting, setRequesting] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["person", id],
    queryFn: () => apiFetch<{ user: any; books: Book[]; reviews: any[] }>(`/api/users/${id}`),
  });

  const requestSwap = async () => {
    haptic("light");
    setRequesting(true);
    try {
      const res = await apiFetch<{ swap: { id: string } }>("/api/swaps", {
        method: "POST",
        body: { receiver_id: id },
      });
      haptic("success");
      router.push(`/swap/${res.swap.id}`);
    } catch (e: any) {
      toast(e.message || "Could not start swap", "error");
    } finally {
      setRequesting(false);
    }
  };

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.backBtn}>
          <ArrowLeft size={22} color={colors.onSurface} />
        </Pressable>
        <AppText variant="heading">Profile</AppText>
        <View style={{ width: 42 }} />
      </View>

      {isLoading || !data ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 60 }} />
      ) : (
        <>
          <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: 120 }} showsVerticalScrollIndicator={false}>
            <View style={{ alignItems: "center", gap: 8 }}>
              <Avatar uri={data.user.avatar_url} name={data.user.name} size={88} />
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <AppText variant="title">{data.user.name}</AppText>
                <ExchangingDot active={data.user.is_exchanging} label />
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
                  <MapPin size={14} color={colors.brandSecondary} weight="fill" />
                  <AppText variant="caption" color={colors.muted}>
                    {data.user.distance_km < 999 ? `${data.user.distance_km} km away` : data.user.neighborhood}
                  </AppText>
                </View>
                <RatingPill rating={data.user.rating} count={data.user.rating_count} />
                <AppText variant="caption" color={colors.muted}>
                  {data.user.swaps_count} swaps
                </AppText>
              </View>
            </View>

            {data.user.bio ? (
              <AppText variant="body" style={{ marginTop: 16, textAlign: "center" }}>
                {data.user.bio}
              </AppText>
            ) : null}

            {data.user.genres?.length > 0 && (
              <View style={styles.tagWrap}>
                {data.user.genres.map((g: string) => (
                  <View key={g} style={styles.tag}>
                    <AppText variant="label" color={colors.onBrandTertiary}>
                      {g}
                    </AppText>
                  </View>
                ))}
              </View>
            )}

            <AppText variant="heading" style={{ marginTop: 24, marginBottom: 12 }}>
              Available to swap ({data.books.length})
            </AppText>
            {data.books.length > 0 ? (
              <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 14 }}>
                {data.books.map((b) => (
                  <BookTile key={b.id} book={b} width={col} onPress={() => router.push(`/book/${b.id}`)} />
                ))}
              </View>
            ) : (
              <AppText variant="body" color={colors.muted}>
                No books listed yet.
              </AppText>
            )}

            <Pressable
              testID="reviews-summary"
              onPress={() => router.push(`/reviews/${id}`)}
              style={styles.reviewsHead}
            >
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <AppText variant="heading">Reviews</AppText>
                <RatingPill rating={data.user.rating} count={data.user.rating_count} />
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                <AppText variant="label" color={colors.brandPrimary}>See all</AppText>
                <ArrowRight size={16} color={colors.brandPrimary} weight="bold" />
              </View>
            </Pressable>
            {data.reviews.length > 0 ? (
              <View style={{ gap: 12, marginTop: 12 }}>
                {data.reviews.slice(0, 2).map((r: any) => (
                  <View key={r.id} style={styles.review}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6 }}>
                      <Avatar uri={r.rater_avatar} name={r.rater_name} size={32} />
                      <AppText variant="label">{r.rater_name}</AppText>
                      <View style={{ marginLeft: "auto" }}>
                        <Stars value={r.stars} />
                      </View>
                    </View>
                    {r.review ? <AppText variant="body" color={colors.muted}>{r.review}</AppText> : null}
                  </View>
                ))}
              </View>
            ) : (
              <AppText variant="body" color={colors.muted} style={{ marginTop: 8 }}>
                No reviews yet — be the first to swap with {data.user.name?.split(" ")[0]}.
              </AppText>
            )}
          </ScrollView>

          <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>
            <Button
              testID="request-swap-button"
              title="Request Swap"
              icon={<ArrowsClockwise size={18} color={colors.onBrandPrimary} weight="bold" />}
              onPress={requestSwap}
              loading={requesting}
            />
          </View>
        </>
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  backBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  tagWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8, justifyContent: "center", marginTop: 16 },
  tag: { backgroundColor: colors.brandTertiary, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999 },
  review: { backgroundColor: colors.surfaceSecondary, borderRadius: 16, padding: 14, borderWidth: 1, borderColor: colors.border },
  reviewsHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 28 },
  footer: { position: "absolute", bottom: 0, left: 0, right: 0, paddingHorizontal: 20, paddingTop: 12, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border },
}));
