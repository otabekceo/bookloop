import { useRef, useState, useCallback } from "react";
import { View, FlatList, Pressable, TextInput, ActivityIndicator, ScrollView } from "react-native";
import { useLocalSearchParams, useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAvoidingView } from "react-native-keyboard-controller";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import BottomSheet, { BottomSheetBackdrop, BottomSheetView } from "@gorhom/bottom-sheet";
import { Image } from "expo-image";
import { ArrowLeft, PaperPlaneRight, ArrowsClockwise, Star, CheckCircle, X, ImageSquare } from "phosphor-react-native";

import { AppText, Avatar, Button, BookCover, Stars, DirectionalIcon, haptic, useToast, useDirectionalStyle } from "@/src/components/ui";
import { apiFetch, resolveImage } from "@/src/api";
import { pickImage, uploadWithProgress, openSettings } from "@/src/media";
import { useAuth } from "@/src/auth";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { renderKeyed, badgeLabel } from "@/src/i18n/messageKeys";

const STATUS_KEYS: Record<string, string> = {
  pending: "swaps.statusPending",
  active: "swaps.statusActive",
  accepted: "swaps.statusActive",
  completed: "swaps.statusCompleted",
  declined: "swaps.statusDeclined",
  cancelled: "swaps.statusCancelled",
};

export default function SwapChat() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const { user } = useAuth();
  const myId = user?.user_id;
  const listRef = useRef<FlatList>(null);
  const proposeSheet = useRef<BottomSheet>(null);
  const rateSheet = useRef<BottomSheet>(null);

  const [text, setText] = useState("");
  const [giveId, setGiveId] = useState<string | null>(null);
  const [getId, setGetId] = useState<string | null>(null);
  const [stars, setStars] = useState(5);
  const [review, setReview] = useState("");

  // Chat bubble "tails" are physical corners; mirror them under RTL so the tail
  // keeps pointing away from the sender's side after the row flips.
  const bubbleMineStyle = useDirectionalStyle(styles.bubbleMine);
  const bubbleTheirsStyle = useDirectionalStyle(styles.bubbleTheirs);
  const imageTailMine = useDirectionalStyle({ borderBottomRightRadius: 4 });
  const imageTailTheirs = useDirectionalStyle({ borderBottomLeftRadius: 4 });

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["swap", id],
    queryFn: () => apiFetch<any>(`/api/swaps/${id}`),
    refetchInterval: 4000,
  });

  const invalidateAll = useCallback(() => {
    refetch();
    qc.invalidateQueries({ queryKey: ["swaps"] });
    qc.invalidateQueries({ queryKey: ["notifications"] });
  }, [refetch, qc]);

  const act = async (fn: () => Promise<any>, hap: "light" | "success" = "light") => {
    try {
      await fn();
      haptic(hap);
      invalidateAll();
    } catch (e: any) {
      toast(e.message || t("swapChat.somethingWentWrong"), "error");
    }
  };

  const send = async () => {
    const body = text.trim();
    if (!body) return;
    setText("");
    await act(() => apiFetch(`/api/swaps/${id}/messages`, { method: "POST", body: { text: body } }));
  };

  const sendImage = async () => {
    const picked = await pickImage("library", (blocked) => {
      toast(blocked ? t("swapChat.enablePhotoAccess") : t("swapChat.permissionNeeded"), "error");
      if (blocked) openSettings();
    });
    if (!picked) return;
    try {
      const up = await uploadWithProgress(picked.uri);
      await act(() => apiFetch(`/api/swaps/${id}/messages`, { method: "POST", body: { image_url: up.url } }), "success");
    } catch (e: any) {
      toast(e.message || t("swapChat.couldNotSendPhoto"), "error");
    }
  };

  const backdrop = useCallback(
    (props: any) => <BottomSheetBackdrop {...props} disappearsOnIndex={-1} appearsOnIndex={0} opacity={0.4} />,
    [],
  );

  if (isLoading || !data) {
    return (
      <View style={[styles.root, { justifyContent: "center" }]}>
        <ActivityIndicator color={colors.brandPrimary} />
      </View>
    );
  }

  const swap = data.swap;
  const other = swap.other_user;
  const status = swap.status;
  const proposal = swap.active_proposal;
  const iAmProposer = proposal && proposal.proposer_id === myId;
  const myRated = swap.is_requester ? swap.requester_rated : swap.receiver_rated;
  const iCompleted = swap.is_requester ? swap.requester_completed : swap.receiver_completed;

  const renderMessage = ({ item }: { item: any }) => {
    if (item.type === "system") {
      return (
        <View style={styles.systemWrap}>
          <AppText variant="caption" color={colors.muted} style={{ textAlign: "center" }}>
            {renderKeyed(t, item)}
          </AppText>
        </View>
      );
    }
    if (item.type === "proposal") {
      const p = item.proposal || {};
      return (
        <View style={styles.proposalCard}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 10 }}>
            <ArrowsClockwise size={16} color={colors.brandPrimary} weight="bold" />
            <AppText variant="label" color={colors.brandPrimary}>
              {t("swapChat.proposalTitle")}
            </AppText>
          </View>
          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 14 }}>
            <View style={{ alignItems: "center", gap: 4, width: 70 }}>
              <BookCover uri={p.offered?.cover_url} width={54} />
              <AppText variant="caption" numberOfLines={1}>
                {p.offered?.title || "?"}
              </AppText>
            </View>
            <ArrowsClockwise size={20} color={colors.muted} />
            <View style={{ alignItems: "center", gap: 4, width: 70 }}>
              <BookCover uri={p.requested?.cover_url} width={54} />
              <AppText variant="caption" numberOfLines={1}>
                {p.requested?.title || "?"}
              </AppText>
            </View>
          </View>
        </View>
      );
    }
    const mine = item.sender_id === myId;
    if (item.type === "image") {
      return (
        <View style={[styles.bubbleRow, { justifyContent: mine ? "flex-end" : "flex-start" }]}>
          <View style={[styles.imageBubble, mine ? imageTailMine : imageTailTheirs]}>
            <Image source={{ uri: resolveImage(item.image_url) }} style={styles.chatImage} contentFit="cover" transition={200} />
            {item.text ? (
              <AppText variant="body" color={colors.onSurface} style={{ marginTop: 6 }}>
                {item.text}
              </AppText>
            ) : null}
          </View>
        </View>
      );
    }
    return (
      <View style={[styles.bubbleRow, { justifyContent: mine ? "flex-end" : "flex-start" }]}>
        <View style={[styles.bubble, mine ? bubbleMineStyle : bubbleTheirsStyle]}>
          <AppText variant="body" color={mine ? colors.onBrandPrimary : colors.onSurfaceSecondary}>
            {item.text}
          </AppText>
        </View>
      </View>
    );
  };

  const showInput = status !== "declined" && status !== "cancelled";

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.backBtn}>
          <DirectionalIcon>
            <ArrowLeft size={22} color={colors.onSurface} />
          </DirectionalIcon>
        </Pressable>
        <Pressable onPress={() => router.push(`/person/${other.user_id}`)} style={{ flexDirection: "row", alignItems: "center", gap: 10, flex: 1 }}>
          <Avatar uri={other.avatar_url} name={other.name} size={38} />
          <View>
            <AppText variant="heading">{other.name}</AppText>
            <AppText variant="caption" color={colors.muted}>
              {STATUS_KEYS[status] ? t(STATUS_KEYS[status]) : status}
            </AppText>
          </View>
        </Pressable>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior="translate-with-padding" keyboardVerticalOffset={0}>
        <FlatList
          ref={listRef}
          data={data.messages}
          keyExtractor={(m) => m.id}
          renderItem={renderMessage}
          contentContainerStyle={{ padding: 16, gap: 8, paddingBottom: 16 }}
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
          showsVerticalScrollIndicator={false}
        />

        {/* Action area */}
        <View style={styles.actionArea}>
          {status === "pending" && (
            <>
              {proposal && !iAmProposer ? (
                <View style={{ flexDirection: "row", gap: 10 }}>
                  <Button testID="decline-swap" title={t("swapChat.decline")} variant="outline" style={{ flex: 1 }} onPress={() => act(() => apiFetch(`/api/swaps/${id}/decline`, { method: "POST" }))} />
                  <Button testID="accept-swap" title={t("swapChat.accept")} style={{ flex: 1 }} onPress={() => act(() => apiFetch(`/api/swaps/${id}/accept`, { method: "POST" }), "success")} />
                </View>
              ) : (
                <View style={{ gap: 8 }}>
                  {iAmProposer && (
                    <AppText variant="caption" color={colors.muted} style={{ textAlign: "center" }}>
                      {t("swapChat.waitingFor", { name: other.name?.split(" ")[0] })}
                    </AppText>
                  )}
                  <Button
                    testID="propose-swap"
                    title={proposal ? t("swapChat.changeProposal") : t("swapChat.proposeSwap")}
                    variant={proposal ? "outline" : "primary"}
                    icon={!proposal ? <ArrowsClockwise size={18} color={colors.onBrandPrimary} weight="bold" /> : undefined}
                    onPress={() => {
                      setGiveId(null);
                      setGetId(null);
                      proposeSheet.current?.expand();
                    }}
                  />
                </View>
              )}
            </>
          )}

          {status === "active" && (
            <View style={{ gap: 8 }}>
              {iCompleted ? (
                <AppText variant="caption" color={colors.muted} style={{ textAlign: "center" }}>
                  {t("swapChat.youMarkedComplete", { name: other.name?.split(" ")[0] })}
                </AppText>
              ) : null}
              <Button
                testID="complete-swap"
                title={t("swapChat.markCompleted")}
                icon={<CheckCircle size={18} color={colors.onBrandPrimary} weight="bold" />}
                disabled={iCompleted}
                onPress={() =>
                  act(async () => {
                    const r = await apiFetch<{ new_badges?: { id: string; label: string }[] }>(`/api/swaps/${id}/complete`, { method: "POST" });
                    if (r.new_badges?.length) toast(t("swapChat.badgeUnlocked", { labels: r.new_badges.map((b) => badgeLabel(t, b)).join(", ") }), "success");
                  }, "success")
                }
              />
            </View>
          )}

          {status === "completed" && (
            myRated ? (
              <View style={styles.doneRow}>
                <CheckCircle size={18} color={colors.brandSecondary} weight="fill" />
                <AppText variant="label" color={colors.brandSecondary}>
                  {t("swapChat.swapCompletedRated")}
                </AppText>
              </View>
            ) : (
              <Button
                testID="rate-swap"
                title={t("swapChat.rateName", { name: other.name?.split(" ")[0] })}
                icon={<Star size={18} color={colors.onBrandPrimary} weight="fill" />}
                onPress={() => rateSheet.current?.expand()}
              />
            )
          )}

          {(status === "declined" || status === "cancelled") && (
            <AppText variant="label" color={colors.muted} style={{ textAlign: "center" }}>
              {t("swapChat.swapWasStatus", { status: STATUS_KEYS[status] ? t(STATUS_KEYS[status]) : status })}
            </AppText>
          )}

          {showInput && (
            <View style={[styles.inputRow, { paddingBottom: insets.bottom > 0 ? insets.bottom : 10 }]}>
              <Pressable testID="attach-image" onPress={sendImage} style={styles.attachBtn}>
                <ImageSquare size={22} color={colors.brandPrimary} weight="regular" />
              </Pressable>
              <TextInput
                testID="message-input"
                value={text}
                onChangeText={setText}
                placeholder={t("swapChat.messagePlaceholder")}
                placeholderTextColor={colors.muted}
                style={styles.input}
                multiline
              />
              <Pressable testID="send-message" onPress={send} style={styles.sendBtn}>
                <PaperPlaneRight size={20} color={colors.onBrandPrimary} weight="fill" />
              </Pressable>
            </View>
          )}
        </View>
      </KeyboardAvoidingView>

      {/* Propose sheet */}
      <BottomSheet ref={proposeSheet} index={-1} snapPoints={[440]} enablePanDownToClose backdropComponent={backdrop} backgroundStyle={{ backgroundColor: colors.surfaceSecondary }} handleIndicatorStyle={{ backgroundColor: colors.borderStrong }}>
        <BottomSheetView style={{ padding: 20, paddingBottom: insets.bottom + 20, gap: 14 }}>
          <AppText variant="title">{t("swapChat.proposeSwap")}</AppText>
          <AppText variant="label" color={colors.muted}>{t("swapChat.youGive")}</AppText>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 10 }}>
            {data.my_books.length === 0 && <AppText variant="body" color={colors.muted}>{t("swapChat.addBooksFirst")}</AppText>}
            {data.my_books.map((b: any) => (
              <Pressable key={b.id} testID={`give-${b.id}`} onPress={() => setGiveId(b.id)} style={[styles.pick, giveId === b.id && styles.pickOn]}>
                <BookCover uri={b.cover_url} width={56} />
                <AppText variant="caption" numberOfLines={1} style={{ width: 56 }}>{b.title}</AppText>
              </Pressable>
            ))}
          </ScrollView>
          <AppText variant="label" color={colors.muted}>{t("swapChat.youReceive")}</AppText>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 10 }}>
            {data.their_books.length === 0 && <AppText variant="body" color={colors.muted}>{t("swapChat.theyHaveNoBooks")}</AppText>}
            {data.their_books.map((b: any) => (
              <Pressable key={b.id} testID={`get-${b.id}`} onPress={() => setGetId(b.id)} style={[styles.pick, getId === b.id && styles.pickOn]}>
                <BookCover uri={b.cover_url} width={56} />
                <AppText variant="caption" numberOfLines={1} style={{ width: 56 }}>{b.title}</AppText>
              </Pressable>
            ))}
          </ScrollView>
          <Button
            testID="send-proposal"
            title={t("swapChat.sendProposal")}
            disabled={!giveId || !getId}
            onPress={() =>
              act(async () => {
                await apiFetch(`/api/swaps/${id}/propose`, { method: "POST", body: { offered_book_id: giveId, requested_book_id: getId } });
                proposeSheet.current?.close();
              }, "success")
            }
          />
        </BottomSheetView>
      </BottomSheet>

      {/* Rate sheet */}
      <BottomSheet ref={rateSheet} index={-1} snapPoints={[380]} enablePanDownToClose backdropComponent={backdrop} backgroundStyle={{ backgroundColor: colors.surfaceSecondary }} handleIndicatorStyle={{ backgroundColor: colors.borderStrong }}>
        <BottomSheetView style={{ padding: 20, paddingBottom: insets.bottom + 20, gap: 16 }}>
          <AppText variant="title">{t("swapChat.rateYourSwap")}</AppText>
          <View style={{ flexDirection: "row", justifyContent: "center", gap: 8 }}>
            {[1, 2, 3, 4, 5].map((n) => (
              <Pressable key={n} testID={`star-${n}`} onPress={() => { haptic("selection"); setStars(n); }}>
                <Star size={40} color={colors.star} weight={n <= stars ? "fill" : "regular"} />
              </Pressable>
            ))}
          </View>
          <TextInput
            testID="review-input"
            value={review}
            onChangeText={setReview}
            placeholder={t("swapChat.addNoteOptional")}
            placeholderTextColor={colors.muted}
            style={styles.reviewInput}
            multiline
          />
          <Button
            testID="submit-rating"
            title={t("swapChat.submitRating")}
            onPress={() =>
              act(async () => {
                await apiFetch(`/api/swaps/${id}/rate`, { method: "POST", body: { stars, review } });
                rateSheet.current?.close();
                toast(t("swapChat.thanksForRating"), "success");
              }, "success")
            }
          />
        </BottomSheetView>
      </BottomSheet>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 16, paddingBottom: 10, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  backBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  systemWrap: { alignItems: "center", paddingVertical: 4 },
  bubbleRow: { flexDirection: "row" },
  bubble: { maxWidth: "78%", paddingHorizontal: 14, paddingVertical: 10, borderRadius: 18 },
  bubbleMine: { backgroundColor: colors.brandPrimary, borderBottomRightRadius: 4 },
  bubbleTheirs: { backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, borderBottomLeftRadius: 4 },
  proposalCard: { alignSelf: "center", backgroundColor: colors.surfaceSecondary, borderRadius: 16, padding: 14, borderWidth: 1, borderColor: colors.brandTertiary, marginVertical: 4 },
  actionArea: { paddingHorizontal: 16, paddingTop: 10, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border, gap: 8 },
  doneRow: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 6 },
  inputRow: { flexDirection: "row", alignItems: "flex-end", gap: 8, paddingTop: 4 },
  attachBtn: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceTertiary },
  imageBubble: { maxWidth: "72%", backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, borderRadius: 18, padding: 6 },
  chatImage: { width: 200, height: 200, borderRadius: 14, backgroundColor: colors.surfaceTertiary },
  input: { flex: 1, backgroundColor: colors.surfaceTertiary, borderRadius: 20, paddingHorizontal: 16, paddingTop: 10, paddingBottom: 10, maxHeight: 100, color: colors.onSurface, fontFamily: "DMSans-Regular", fontSize: 15 },
  sendBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  pick: { alignItems: "center", gap: 4, padding: 6, borderRadius: 12, borderWidth: 2, borderColor: "transparent" },
  pickOn: { borderColor: colors.brandPrimary, backgroundColor: colors.brandTertiary },
  reviewInput: { backgroundColor: colors.surfaceTertiary, borderRadius: 12, padding: 14, minHeight: 70, color: colors.onSurface, fontFamily: "DMSans-Regular", fontSize: 15 },
}));
