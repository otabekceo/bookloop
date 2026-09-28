import { useRef, useState } from "react";
import { View, ScrollView, Pressable, useWindowDimensions, Share } from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import * as Clipboard from "expo-clipboard";
import { MapPin, PencilSimple, SignOut, UserPlus, Copy, ShareNetwork, Sparkle, CaretRight, Translate, CircleHalf, Sun, Moon } from "phosphor-react-native";

import { AppText, Avatar, Button, RatingPill, DirectionalIcon, haptic, useToast } from "@/src/components/ui";
import { BookTile, Book } from "@/src/components/cards";
import { BadgeGrid } from "@/src/components/badges";
import { useAuth, type User } from "@/src/auth";
import { apiFetch } from "@/src/api";
import { useAppearance } from "@/src/appearance";
import { useDeviceLocation } from "@/src/location";
import { enumLabel } from "@/src/i18n/enums";
import { useLanguage, useLanguageMeta } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

export default function Profile() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, setUser, logout, refreshUser } = useAuth();
  const { t } = useLanguage();
  const meta = useLanguageMeta();
  const { preference: appearancePreference } = useAppearance();
  const location = useDeviceLocation();
  const toast = useToast();
  const qc = useQueryClient();
  const { width } = useWindowDimensions();
  const col = (width - 40 - 14) / 2;
  const [saving, setSaving] = useState(false);
  // A plain closure lock, not just `saving` state: `disabled={saving}` only takes effect after a
  // React re-render, leaving a window where a rapid second tap fires before that lands. Same fix
  // shape as the busy-lock in src/i18n/languageSwitch.ts for the identical class of problem.
  const exchangingLock = useRef(false);

  const { data: booksData, refetch } = useQuery({
    queryKey: ["myBooks", "All"],
    queryFn: () => apiFetch<{ books: Book[] }>(`/api/books?status=All`),
  });

  useFocusEffect(
    useCallback(() => {
      refreshUser();
      refetch();
    }, [refreshUser, refetch]),
  );

  if (!user) return null;
  const books = (booksData?.books || []).slice(0, 4);

  const toggleExchanging = async () => {
    if (exchangingLock.current) return; // rapid second tap: ignored, not queued
    exchangingLock.current = true;
    haptic("light");
    const original = user.is_exchanging;
    const next = !original;
    setUser({ ...user, is_exchanging: next }); // immediate ON/OFF — don't wait on the network for this
    setSaving(true);
    try {
      const data = await apiFetch<{ user: User }>("/api/users/me", { method: "PUT", body: { is_exchanging: next } });
      setUser(data.user); // reconcile with the server's own response (no separate refetch needed)
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["clusters"] });
      toast(next ? t("profile.nowExchanging") : t("profile.exchangingPaused"), "success");
    } catch {
      setUser({ ...user, is_exchanging: original }); // revert the optimistic flip
      toast(t("profile.couldNotUpdateExchangeStatus"), "error");
    } finally {
      setSaving(false);
      exchangingLock.current = false;
    }
  };

  const inviteLink = `${process.env.EXPO_PUBLIC_BACKEND_URL}?ref=${user.user_id}`;
  const inviteMessage = `📚 Join me on BookLoop — discover readers near you and swap books locally. Bring your reading group into your local loop!\n\n${inviteLink}`;

  const shareInvite = async () => {
    haptic("light");
    try {
      await Share.share({ message: inviteMessage });
    } catch {}
  };

  const copyInvite = async () => {
    haptic("selection");
    await Clipboard.setStringAsync(inviteLink);
    toast(t("profile.inviteCopied"), "success");
  };

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={{ paddingTop: insets.top + 16, paddingBottom: 40 }}
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.headerRow}>
        <Pressable testID="edit-profile-button" onPress={() => router.push("/edit-profile")} style={styles.iconBtn}>
          <PencilSimple size={20} color={colors.onSurface} />
        </Pressable>
        <Pressable testID="logout-button" onPress={logout} style={styles.iconBtn}>
          <SignOut size={20} color={colors.error} />
        </Pressable>
      </View>

      <View style={styles.top}>
        <Avatar uri={user.avatar_url} name={user.name} size={96} />
        <AppText variant="title" style={{ marginTop: 12 }}>
          {user.name}
        </AppText>
        {/* Location comes only from the device: tapping it re-reads the position (and asks for the
            permission if it was never granted, or opens Settings once it's blocked). */}
        <Pressable
          testID="profile-location"
          onPress={location.state === "blocked" ? location.openSettings : location.request}
          disabled={location.state === "locating"}
          style={{ flexDirection: "row", alignItems: "center", flexWrap: "wrap", justifyContent: "center", gap: 4, marginTop: 2, paddingHorizontal: 20 }}
        >
          <MapPin size={14} color={colors.brandSecondary} weight="fill" />
          <AppText variant="body" color={colors.muted} style={{ textAlign: "center" }}>
            {location.state === "locating"
              ? t("location.locating")
              : location.hasLocation
                ? [user.neighborhood, user.city].filter(Boolean).join(", ") || t("location.detected")
                : t("location.notSet")}
          </AppText>
          {!location.hasLocation && location.state !== "locating" && (
            <AppText variant="label" color={colors.brandPrimary}>
              {location.state === "blocked" ? t("location.openSettings") : t("location.useMyLocation")}
            </AppText>
          )}
        </Pressable>
        {!location.hasLocation && (location.state === "denied" || location.state === "servicesOff" || location.state === "error" || location.state === "blocked") && (
          <AppText variant="caption" color={colors.muted} style={{ textAlign: "center", marginTop: 4, paddingHorizontal: 24 }}>
            {location.state === "denied"
              ? t("location.deniedBody")
              : location.state === "blocked"
                ? t("location.blockedBody")
                : location.state === "servicesOff"
                  ? t("location.servicesOffBody")
                  : t("location.errorBody")}
          </AppText>
        )}
      </View>

      <View style={styles.stats}>
        <View style={styles.statCell}>
          <RatingPill rating={user.rating} />
          <AppText variant="caption" color={colors.muted}>
            {t("profile.reviewsCount", { count: user.rating_count })}
          </AppText>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statCell}>
          <AppText variant="heading">{user.swaps_count}</AppText>
          <AppText variant="caption" color={colors.muted}>
            {t("profile.swapsDone")}
          </AppText>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statCell}>
          <AppText variant="heading">{booksData?.books?.length ?? 0}</AppText>
          <AppText variant="caption" color={colors.muted}>
            {t("profile.booksCount")}
          </AppText>
        </View>
      </View>

      {user.bio ? (
        <AppText variant="body" color={colors.onSurface} style={styles.bio}>
          {user.bio}
        </AppText>
      ) : null}

      {/* Exchanging toggle */}
      <Pressable testID="exchanging-switch" onPress={toggleExchanging} disabled={saving} style={styles.toggleCard}>
        <View style={{ flex: 1 }}>
          <AppText variant="heading">{t("profile.currentlyExchanging")}</AppText>
          <AppText variant="caption" color={colors.muted}>
            {user.is_exchanging ? t("profile.visibleNearby") : t("profile.hiddenFromDiscovery")}
          </AppText>
        </View>
        <View style={[styles.switch, user.is_exchanging && styles.switchOn]}>
          <View style={[styles.knob, user.is_exchanging && styles.knobOn]} />
        </View>
      </Pressable>

      {/* Invite friends */}
      <View style={styles.inviteCard}>
        <View style={styles.inviteIcon}>
          <UserPlus size={22} color={colors.onBrandSecondary} weight="fill" />
        </View>
        <View style={{ flex: 1 }}>
          <AppText variant="heading">{t("profile.inviteTitle")}</AppText>
          <AppText variant="caption" color={colors.muted}>
            {t("profile.inviteBody")}
          </AppText>
        </View>
      </View>
      <View style={styles.inviteBtnRow}>
        <Pressable testID="share-invite" onPress={shareInvite} style={styles.inviteShare}>
          <ShareNetwork size={18} color={colors.onBrandPrimary} weight="fill" />
          <AppText variant="button" color={colors.onBrandPrimary}>{t("profile.shareInvite")}</AppText>
        </Pressable>
        <Pressable testID="copy-invite" onPress={copyInvite} style={styles.inviteCopy}>
          <Copy size={18} color={colors.onSurface} />
        </Pressable>
      </View>

      {/* Swap badges */}
      <Section title={t("profile.swapBadges")}>
        <BadgeGrid badges={user.badges || []} swapsCount={user.swaps_count} />
      </Section>

      {/* Wishlist */}
      <Pressable testID="wishlist-card" onPress={() => router.push("/wishlist")} style={styles.wishCard}>
        <View style={styles.wishIcon}>
          <Sparkle size={22} color={colors.onBrandPrimary} weight="fill" />
        </View>
        <View style={{ flex: 1 }}>
          <AppText variant="heading">{t("profile.myWishlist")}</AppText>
          <AppText variant="caption" color={colors.muted}>
            {user.genres.length > 0
              ? `${t("common.genreCount", { count: user.genres.length })} · ${user.reading_interests?.length || 0} · ${t("common.seeAll")}`
              : t("wishlist.subtitle")}
          </AppText>
        </View>
        <DirectionalIcon>
          <CaretRight size={18} color={colors.muted} weight="bold" />
        </DirectionalIcon>
      </Pressable>

      {/* Genres */}
      <Section title={t("profile.interestedIn")}>
        {user.genres.length > 0 ? (
          <View style={styles.tagWrap}>
            {user.genres.map((g) => (
              <View key={g} style={styles.tag}>
                <AppText variant="label" color={colors.onBrandTertiary}>
                  {enumLabel(t, "genre", g)}
                </AppText>
              </View>
            ))}
          </View>
        ) : (
          <AppText variant="body" color={colors.muted}>
            {t("profile.addGenresHint")}
          </AppText>
        )}
      </Section>

      {/* Reading interests */}
      {(user.reading_interests?.length || 0) > 0 && (
        <Section title={t("profile.readingInterests")}>
          <View style={styles.tagWrap}>
            {user.reading_interests.map((i) => (
              <View key={i} style={[styles.tag, { backgroundColor: colors.surfaceTertiary }]}>
                <AppText variant="label" color={colors.onSurfaceTertiary}>
                  {enumLabel(t, "interest", i)}
                </AppText>
              </View>
            ))}
          </View>
        </Section>
      )}

      {/* Languages */}
      <Section title={t("profile.languages")}>
        <View style={styles.tagWrap}>
          {user.languages.map((l) => (
            <View key={l} style={[styles.tag, { backgroundColor: colors.sageSoft }]}>
              <AppText variant="label" color={colors.brandSecondary}>
                {enumLabel(t, "bookLanguage", l)}
              </AppText>
            </View>
          ))}
        </View>
      </Section>

      {/* My books */}
      <Section title={t("profile.myBooks")} onSeeAll={() => router.push("/(tabs)/books")}>
        {books.length > 0 ? (
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 14 }}>
            {books.map((b) => (
              <BookTile key={b.id} book={b} width={col} showStatus onPress={() => router.push(`/book/${b.id}`)} />
            ))}
          </View>
        ) : (
          <Button title={t("profile.addBook")} variant="outline" onPress={() => router.push("/book/add")} testID="profile-add-book" />
        )}
      </Section>

      {/* App language */}
      <Pressable testID="language-settings" onPress={() => router.push("/settings/language")} style={styles.langCard}>
        <View style={styles.langIcon}>
          <Translate size={22} color={colors.onBrandPrimary} weight="fill" />
        </View>
        <View style={{ flex: 1 }}>
          <AppText variant="heading">{t("common.language")}</AppText>
          <AppText variant="caption" color={colors.muted}>
            {meta.nativeName}
          </AppText>
        </View>
        <DirectionalIcon>
          <CaretRight size={18} color={colors.muted} weight="bold" />
        </DirectionalIcon>
      </Pressable>

      {/* Appearance: the label opens the full System/Light/Dark picker (settings/appearance.tsx); the
          switch on the right is the quick Light<->Dark flip. Siblings, not nested Pressables, so a
          tap on the switch never also triggers the row's navigation. */}
      <View style={[styles.langCard, { marginTop: 12 }]}>
        <Pressable
          testID="appearance-settings"
          onPress={() => router.push("/settings/appearance")}
          style={{ flex: 1, flexDirection: "row", alignItems: "center", gap: 14 }}
        >
          <View style={styles.langIcon}>
            <CircleHalf size={22} color={colors.onBrandPrimary} weight="fill" />
          </View>
          <View style={{ flex: 1 }}>
            <AppText variant="heading">{t("appearance.title")}</AppText>
            <AppText variant="caption" color={colors.muted}>
              {t(`appearance.${appearancePreference}`)}
            </AppText>
          </View>
        </Pressable>
        <QuickAppearanceToggle />
      </View>
    </ScrollView>
  );
}

/**
 * Sun/switch/moon quick toggle: Light<->Dark only (the full System option lives in the Appearance
 * settings screen this row's label opens). Reuses the exact switch/knob styling the "Currently
 * exchanging" toggle above already uses — no separate visual language. Purely a re-render, same as
 * the full settings screen: no reload, applies instantly.
 */
function QuickAppearanceToggle() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const { resolvedScheme, setPreference } = useAppearance();
  const isDark = resolvedScheme === "dark";
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
      <Sun size={16} color={isDark ? colors.muted : colors.brandPrimary} weight={isDark ? "regular" : "fill"} />
      <Pressable
        testID="quick-appearance-toggle"
        accessibilityRole="switch"
        accessibilityState={{ checked: isDark }}
        accessibilityLabel={t("appearance.title")}
        onPress={() => {
          haptic("selection");
          setPreference(isDark ? "light" : "dark");
        }}
        style={[styles.switch, isDark && styles.switchOn]}
      >
        <View style={[styles.knob, isDark && styles.knobOn]} />
      </Pressable>
      <Moon size={16} color={isDark ? colors.brandPrimary : colors.muted} weight={isDark ? "fill" : "regular"} />
    </View>
  );
}

function Section({ title, children, onSeeAll }: { title: string; children: React.ReactNode; onSeeAll?: () => void }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  return (
    <View style={styles.section}>
      <View style={styles.sectionHead}>
        <AppText variant="heading">{title}</AppText>
        {onSeeAll && (
          <Pressable onPress={onSeeAll}>
            <AppText variant="label" color={colors.brandPrimary}>
              {t("profile.seeAll")}
            </AppText>
          </Pressable>
        )}
      </View>
      {children}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  headerRow: { flexDirection: "row", justifyContent: "flex-end", gap: 10, paddingHorizontal: 20 },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  top: { alignItems: "center", marginTop: 4 },
  stats: { flexDirection: "row", alignItems: "center", marginHorizontal: 20, marginTop: 20, backgroundColor: colors.surfaceSecondary, borderRadius: 20, paddingVertical: 16, borderWidth: 1, borderColor: colors.border },
  statCell: { flex: 1, alignItems: "center", gap: 4 },
  statDivider: { width: 1, height: 32, backgroundColor: colors.border },
  bio: { marginHorizontal: 20, marginTop: 16 },
  toggleCard: { flexDirection: "row", alignItems: "center", marginHorizontal: 20, marginTop: 16, backgroundColor: colors.surfaceSecondary, borderRadius: 18, padding: 16, borderWidth: 1, borderColor: colors.border },
  switch: { width: 50, height: 30, borderRadius: 15, backgroundColor: colors.surfaceTertiary, padding: 3, justifyContent: "center" },
  switchOn: { backgroundColor: colors.brandSecondary },
  knob: { width: 24, height: 24, borderRadius: 12, backgroundColor: "#FFFFFF" },
  knobOn: { alignSelf: "flex-end" },
  section: { marginHorizontal: 20, marginTop: 24, gap: 12 },
  inviteCard: { flexDirection: "row", alignItems: "center", gap: 14, marginHorizontal: 20, marginTop: 16, backgroundColor: colors.sageSoft, borderRadius: 18, padding: 16 },
  inviteIcon: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.brandSecondary, alignItems: "center", justifyContent: "center" },
  inviteBtnRow: { flexDirection: "row", gap: 10, marginHorizontal: 20, marginTop: 10 },
  inviteShare: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, backgroundColor: colors.brandPrimary, borderRadius: 14, height: 50 },
  inviteCopy: { width: 50, height: 50, borderRadius: 14, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  wishCard: { flexDirection: "row", alignItems: "center", gap: 14, marginHorizontal: 20, marginTop: 24, backgroundColor: colors.brandTertiary, borderRadius: 18, padding: 16 },
  wishIcon: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  langCard: { flexDirection: "row", alignItems: "center", gap: 14, marginHorizontal: 20, marginTop: 24, backgroundColor: colors.surfaceSecondary, borderRadius: 18, padding: 16, borderWidth: 1, borderColor: colors.border },
  langIcon: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  tagWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  tag: { backgroundColor: colors.brandTertiary, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999 },
}));
