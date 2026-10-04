import { useState } from "react";
import { View, ScrollView, Pressable, ActivityIndicator, useWindowDimensions } from "react-native";
import { useLocalSearchParams, useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, MapPin, ArrowsClockwise, ArrowRight, Sparkle, ChatCircleDots, PauseCircle } from "phosphor-react-native";

import { ReviewCard } from "@/src/components/ReviewCard";
import { AppText, Avatar, Button, RatingPill, ExchangingDot, EmptyState, DirectionalIcon, haptic, useToast } from "@/src/components/ui";
import { BookTile, Book } from "@/src/components/cards";
import { BadgeChips } from "@/src/components/badges";
import { FavoriteButton } from "@/src/components/FavoriteButton";
import { useAuth } from "@/src/auth";
import type { SwapWith } from "@/src/swapFlow";
import { apiFetch } from "@/src/api";
import { distanceOrArea } from "@/src/distance";
import { openOrRequestSwap } from "@/src/swapStart";
import { enumLabel } from "@/src/i18n/enums";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";

export default function PersonProfile() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const { width } = useWindowDimensions();
  const col = (width - 40 - 14) / 2;
  // Pushes the star rating to the trailing edge; `marginLeft: auto` does not
  // auto-mirror, so swap it under RTL.
  const [requesting, setRequesting] = useState(false);
  const { user: me } = useAuth();
  const isMe = me?.user_id === id;

  const { data, isLoading } = useQuery({
    queryKey: ["person", id],
    queryFn: () => apiFetch<{ user: any; books: Book[]; reviews: any[] }>(`/api/users/${id}`),
  });
  // What this viewer may do with this reader: an open swap, a conversation still open to chat
  // (previous partners), and whether new proposals are accepted ("Currently exchanging").
  const { data: rel } = useQuery({
    queryKey: ["swapWith", id],
    enabled: !isMe,
    queryFn: () => apiFetch<SwapWith>(`/api/swaps/with/${encodeURIComponent(id)}`),
  });
  const chatSwapId = rel?.open?.id || (rel?.latest_chat_open ? rel?.latest?.id : undefined);
  const canPropose = data?.user?.is_exchanging !== false && rel?.can_propose !== false;

  const requestSwap = async () => {
    haptic("light");
    setRequesting(true);
    try {
      await openOrRequestSwap({
        userId: id,
        name: data?.user?.name?.split(" ")[0] || "",
        t,
        open: (swapId) => {
          haptic("success");
          router.push(`/swap/${swapId}`);
        },
        compose: (userId) => router.push({ pathname: "/swap/new", params: { userId } }),
        onError: (message) => toast(message || t("person.couldNotStartSwap"), "error"),
      });
    } catch (e: any) {
      toast(e.message || t("person.couldNotStartSwap"), "error");
    } finally {
      setRequesting(false);
    }
  };

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.backBtn}>
          <DirectionalIcon>
            <ArrowLeft size={22} color={colors.onSurface} />
          </DirectionalIcon>
        </Pressable>
        <AppText variant="heading">{t("person.title")}</AppText>
        {isMe || !data ? (
          <View style={{ width: 42 }} />
        ) : (
          <FavoriteButton userId={id} isFavorite={!!data.user.is_favorite} style={styles.backBtn} />
        )}
      </View>

      {isLoading || !data ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 60 }} />
      ) : (
        <>
          <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: 190 }} showsVerticalScrollIndicator={false}>
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
                    {distanceOrArea(t, data.user)}
                  </AppText>
                </View>
                <RatingPill rating={data.user.rating} count={data.user.rating_count} />
                <AppText variant="caption" color={colors.muted}>
                  {t("common.swapCount", { count: data.user.swaps_count })}
                </AppText>
              </View>
            </View>

            <View style={{ marginTop: 10 }}>
              <BadgeChips badges={data.user.badges} />
            </View>

            {data.user.bio ? (
              <AppText variant="body" style={{ marginTop: 16, textAlign: "center" }}>
                {data.user.bio}
              </AppText>
            ) : null}

            {(data.user.shared_genres?.length || 0) > 0 && (
              <View testID="shared-genres" style={styles.sharedRow}>
                <Sparkle size={14} color={colors.brandPrimary} weight="fill" />
                <AppText variant="caption" color={colors.brandPrimary}>
                  {t("person.youBothLove", { genres: data.user.shared_genres.map((g: string) => enumLabel(t, "genre", g)).join(", ") })}
                </AppText>
              </View>
            )}

            {data.user.genres?.length > 0 && (
              <View style={styles.tagWrap}>
                {data.user.genres.map((g: string) => {
                  const hit = (data.user.shared_genres || []).includes(g);
                  return (
                    <View key={g} style={[styles.tag, !hit && { backgroundColor: colors.surfaceTertiary }]}>
                      <AppText variant="label" color={hit ? colors.onBrandTertiary : colors.onSurfaceTertiary}>
                        {enumLabel(t, "genre", g)}
                      </AppText>
                    </View>
                  );
                })}
              </View>
            )}

            <AppText variant="heading" style={{ marginTop: 24, marginBottom: 12 }}>
              {t("person.availableToSwap", { count: data.books.length })}
            </AppText>
            {data.books.length > 0 ? (
              <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 14 }}>
                {data.books.map((b) => (
                  <BookTile key={b.id} book={b} width={col} onPress={() => router.push(`/book/${b.id}`)} />
                ))}
              </View>
            ) : (
              <AppText variant="body" color={colors.muted}>
                {t("person.noBooksListed")}
              </AppText>
            )}

            <Pressable
              testID="reviews-summary"
              onPress={() => router.push(`/reviews/${id}`)}
              style={styles.reviewsHead}
            >
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <AppText variant="heading">{t("person.reviews")}</AppText>
                <RatingPill rating={data.user.rating} count={data.user.rating_count} />
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                <AppText variant="label" color={colors.brandPrimary}>{t("person.seeAll")}</AppText>
                <DirectionalIcon>
                  <ArrowRight size={16} color={colors.brandPrimary} weight="bold" />
                </DirectionalIcon>
              </View>
            </Pressable>
            {data.reviews.length > 0 ? (
              <View style={{ gap: 12, marginTop: 12 }}>
                {data.reviews.slice(0, 2).map((r: any) => (
                  <ReviewCard key={r.id} review={r} compact />
                ))}
              </View>
            ) : (
              <AppText variant="body" color={colors.muted} style={{ marginTop: 8 }}>
                {t("person.noReviews", { name: data.user.name?.split(" ")[0] })}
              </AppText>
            )}
          </ScrollView>

          {!isMe && (
            <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>
              {canPropose ? (
                <Button
                  testID="request-swap-button"
                  title={rel?.open ? t("person.openChat") : t("person.requestSwap")}
                  icon={<ArrowsClockwise size={18} color={colors.onBrandPrimary} weight="bold" />}
                  onPress={requestSwap}
                  loading={requesting}
                />
              ) : (
                <View style={{ gap: 10 }}>
                  <View testID="not-accepting-exchanges" style={styles.pausedRow}>
                    <PauseCircle size={20} color={colors.muted} />
                    <View style={{ flex: 1 }}>
                      <AppText variant="label">{t("person.notAccepting")}</AppText>
                      <AppText variant="caption" color={colors.muted}>
                        {t("person.notAcceptingBody", { name: data.user.name?.split(" ")[0] })}
                      </AppText>
                    </View>
                  </View>
                  {chatSwapId && (
                    <Button
                      testID="open-chat-button"
                      title={t("person.openChat")}
                      variant="outline"
                      icon={<ChatCircleDots size={18} color={colors.brandPrimary} weight="bold" />}
                      onPress={() => router.push(`/swap/${chatSwapId}`)}
                    />
                  )}
                </View>
              )}
            </View>
          )}
        </>
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  backBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  tagWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8, justifyContent: "center", marginTop: 12 },
  sharedRow: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 16 },
  tag: { backgroundColor: colors.brandTertiary, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999 },
  review: { backgroundColor: colors.surfaceSecondary, borderRadius: 16, padding: 14, borderWidth: 1, borderColor: colors.border },
  reviewsHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 28 },
  footer: { position: "absolute", bottom: 0, left: 0, right: 0, paddingHorizontal: 20, paddingTop: 12, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border },
  pausedRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.surfaceTertiary, borderRadius: 14, padding: 12 },
}));
