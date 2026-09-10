import { useState } from "react";
import { View, ScrollView, Pressable, useWindowDimensions, Share } from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import * as Clipboard from "expo-clipboard";
import { MapPin, PencilSimple, SignOut, UserPlus, Copy, ShareNetwork } from "phosphor-react-native";

import { AppText, Avatar, Button, RatingPill, haptic, useToast } from "@/src/components/ui";
import { BookTile, Book } from "@/src/components/cards";
import { useAuth } from "@/src/auth";
import { apiFetch } from "@/src/api";
import { makeStyles, useTheme } from "@/src/theme";

export default function Profile() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, logout, refreshUser } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const { width } = useWindowDimensions();
  const col = (width - 40 - 14) / 2;
  const [saving, setSaving] = useState(false);

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

  const toggleExchanging = async () => {    haptic("light");
    setSaving(true);
    try {
      const next = !user.is_exchanging;
      await apiFetch("/api/users/me", { method: "PUT", body: { is_exchanging: next } });
      await refreshUser();
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["clusters"] });
      toast(next ? "You're now exchanging" : "Exchanging paused", "success");
    } catch {
      toast("Could not update", "error");
    } finally {
      setSaving(false);
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
    toast("Invite link copied", "success");
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
        <View style={{ flexDirection: "row", alignItems: "center", gap: 4, marginTop: 2 }}>
          <MapPin size={14} color={colors.brandSecondary} weight="fill" />
          <AppText variant="body" color={colors.muted}>
            {user.neighborhood}, {user.city}
          </AppText>
        </View>
      </View>

      <View style={styles.stats}>
        <View style={styles.statCell}>
          <RatingPill rating={user.rating} />
          <AppText variant="caption" color={colors.muted}>
            {user.rating_count} reviews
          </AppText>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statCell}>
          <AppText variant="heading">{user.swaps_count}</AppText>
          <AppText variant="caption" color={colors.muted}>
            swaps done
          </AppText>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statCell}>
          <AppText variant="heading">{booksData?.books?.length ?? 0}</AppText>
          <AppText variant="caption" color={colors.muted}>
            books
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
          <AppText variant="heading">Currently exchanging</AppText>
          <AppText variant="caption" color={colors.muted}>
            {user.is_exchanging ? "Visible to readers nearby" : "Hidden from discovery"}
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
          <AppText variant="heading">Invite friends</AppText>
          <AppText variant="caption" color={colors.muted}>
            Bring your reading group into your local loop
          </AppText>
        </View>
      </View>
      <View style={styles.inviteBtnRow}>
        <Pressable testID="share-invite" onPress={shareInvite} style={styles.inviteShare}>
          <ShareNetwork size={18} color={colors.onBrandPrimary} weight="fill" />
          <AppText variant="button" color={colors.onBrandPrimary}>Share invite</AppText>
        </Pressable>
        <Pressable testID="copy-invite" onPress={copyInvite} style={styles.inviteCopy}>
          <Copy size={18} color={colors.onSurface} />
        </Pressable>
      </View>

      {/* Genres */}
      <Section title="Interested in">
        {user.genres.length > 0 ? (
          <View style={styles.tagWrap}>
            {user.genres.map((g) => (
              <View key={g} style={styles.tag}>
                <AppText variant="label" color={colors.onBrandTertiary}>
                  {g}
                </AppText>
              </View>
            ))}
          </View>
        ) : (
          <AppText variant="body" color={colors.muted}>
            Add genres you love from Edit profile.
          </AppText>
        )}
      </Section>

      {/* Languages */}
      <Section title="Languages">
        <View style={styles.tagWrap}>
          {user.languages.map((l) => (
            <View key={l} style={[styles.tag, { backgroundColor: colors.sageSoft }]}>
              <AppText variant="label" color={colors.brandSecondary}>
                {l}
              </AppText>
            </View>
          ))}
        </View>
      </Section>

      {/* My books */}
      <Section title="My books" onSeeAll={() => router.push("/(tabs)/books")}>
        {books.length > 0 ? (
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 14 }}>
            {books.map((b) => (
              <BookTile key={b.id} book={b} width={col} showStatus onPress={() => router.push(`/book/${b.id}`)} />
            ))}
          </View>
        ) : (
          <Button title="Add a book" variant="outline" onPress={() => router.push("/book/add")} testID="profile-add-book" />
        )}
      </Section>
    </ScrollView>
  );
}

function Section({ title, children, onSeeAll }: { title: string; children: React.ReactNode; onSeeAll?: () => void }) {
  const styles = useStyles();
  const { colors } = useTheme();
  return (
    <View style={styles.section}>
      <View style={styles.sectionHead}>
        <AppText variant="heading">{title}</AppText>
        {onSeeAll && (
          <Pressable onPress={onSeeAll}>
            <AppText variant="label" color={colors.brandPrimary}>
              See all
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
  tagWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  tag: { backgroundColor: colors.brandTertiary, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999 },
}));
