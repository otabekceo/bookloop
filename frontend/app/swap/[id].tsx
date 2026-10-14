import { useRef, useState, useCallback, useMemo, useEffect } from "react";
import { View, FlatList, Pressable, TextInput, ActivityIndicator, useWindowDimensions, type LayoutChangeEvent, type NativeScrollEvent, type NativeSyntheticEvent } from "react-native";
import { useLocalSearchParams, useRouter, Stack, useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAvoidingView } from "react-native-keyboard-controller";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import BottomSheet, { BottomSheetBackdrop, BottomSheetScrollView, BottomSheetTextInput } from "@gorhom/bottom-sheet";
import { Image } from "expo-image";
import { ArrowLeft, PaperPlaneRight, ArrowsClockwise, Star, CheckCircle, ImageSquare, Quotes, LockSimple, ChatCircleDots, UserCircle } from "phosphor-react-native";

import { AppText, Avatar, Button, BookCover, DirectionalIcon, haptic, useToast, useDirectionalStyle } from "@/src/components/ui";
import { SafetyMenuButton } from "@/src/components/Safety";
import { displayName } from "@/src/people";
import { apiFetch, resolveImage } from "@/src/api";
import { pickImage, uploadWithProgress, openSettings } from "@/src/media";
import { useAuth } from "@/src/auth";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { renderKeyed, badgeLabel } from "@/src/i18n/messageKeys";
import { mergeMessages, newClientId, withOutbox, type OutboxItem } from "@/src/chatMessages";
import { chatPanel } from "@/src/swapFlow";
import { rateBody, REVIEW_MAX_CHARS } from "@/src/reviews";
import { BookPicker } from "@/src/components/BookPicker";
import { setActiveSwap } from "@/src/notifications";

type ChatData = { swap: any; messages: any[]; my_books: any[]; their_books: any[] };

const STATUS_KEYS: Record<string, string> = {
  pending: "swaps.statusPending",
  active: "swaps.statusActive",
  accepted: "swaps.statusActive",
  completed: "swaps.statusCompleted",
  declined: "swaps.statusDeclined",
  cancelled: "swaps.statusCancelled",
};

// How close to the bottom (px) still counts as "reading the latest messages".
const NEAR_END_PX = 80;
// Clear space kept between the composer and the top of the keyboard.
const KEYBOARD_GAP = 8;

export default function SwapChat() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const { width: screenWidth } = useWindowDimensions();
  // A photo fills its bubble (72% of the list width minus the bubble's padding), capped at 200: a fixed
  // 200 overflowed the bubble on 320 dp phones.
  const chatImageSize = Math.min(200, Math.floor((screenWidth - 32) * 0.72) - 12);
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const { user } = useAuth();
  const myId = user?.user_id;
  const listRef = useRef<FlatList>(null);
  const proposeSheet = useRef<BottomSheet>(null);
  const rateSheet = useRef<BottomSheet>(null);

  const [text, setText] = useState("");
  // Mirrors the input synchronously: two taps on Send within one frame must not both read the same text.
  const textRef = useRef("");
  const [outbox, setOutbox] = useState<OutboxItem[]>([]);
  const forceFull = useRef(false);
  const [giveId, setGiveId] = useState<string | null>(null);
  const [getId, setGetId] = useState<string | null>(null);
  const [stars, setStars] = useState(5);
  const [review, setReview] = useState("");
  const [anonymous, setAnonymous] = useState(false);
  const [focused, setFocused] = useState(false);
  const nearEnd = useRef(true);
  const listHeight = useRef(0);
  const reportedRead = useRef<string | null>(null);

  // Chat bubble "tails" are physical corners; mirror them under RTL so the tail
  // keeps pointing away from the sender's side after the row flips.
  const bubbleMineStyle = useDirectionalStyle(styles.bubbleMine);
  const bubbleTheirsStyle = useDirectionalStyle(styles.bubbleTheirs);
  const imageTailMine = useDirectionalStyle({ borderBottomRightRadius: 4 });
  const imageTailTheirs = useDirectionalStyle({ borderBottomLeftRadius: 4 });

  // First load gets the whole conversation; every poll after that asks only for messages newer than
  // the last one we have (?after=) and merges them in, instead of re-downloading everything every 4 s.
  const { data, isLoading, refetch } = useQuery<ChatData>({
    queryKey: ["swap", id],
    queryFn: async () => {
      const before = qc.getQueryData<ChatData>(["swap", id]);
      const lastId = forceFull.current ? undefined : before?.messages[before.messages.length - 1]?.id;
      forceFull.current = false;
      const res = await apiFetch<any>(`/api/swaps/${id}${lastId ? `?after=${encodeURIComponent(lastId)}` : ""}`);
      // Merge into what is cached NOW, not what was cached when the request started: a send that
      // completed meanwhile has already been written into the cache and must not be dropped.
      const current = qc.getQueryData<ChatData>(["swap", id]);
      if (res.full || !current) {
        return {
          swap: res.swap,
          messages: mergeMessages(res.messages, current ? current.messages.filter((m) => m.created_at > (res.messages[res.messages.length - 1]?.created_at ?? "")) : []),
          my_books: res.my_books ?? current?.my_books ?? [],
          their_books: res.their_books ?? current?.their_books ?? [],
        };
      }
      return { ...current, swap: res.swap, messages: mergeMessages(current.messages, res.messages) };
    },
    refetchInterval: 4000,
  });

  const invalidateAll = useCallback(() => {
    forceFull.current = true; // actions change status/books: reload the whole conversation once
    refetch();
    qc.invalidateQueries({ queryKey: ["swaps"] });
    qc.invalidateQueries({ queryKey: ["notifications"] });
    qc.invalidateQueries({ queryKey: ["notificationFeed"] });
  }, [refetch, qc]);

  // While this conversation is on screen: pushes about it aren't shown on top of it, and what is on
  // screen counts as read (only then — loading the app or the lists never marks anything read).
  useFocusEffect(
    useCallback(() => {
      setFocused(true);
      setActiveSwap(id);
      return () => {
        setFocused(false);
        setActiveSwap(null);
      };
    }, [id]),
  );

  const lastServerId = data?.messages[data.messages.length - 1]?.id;
  const unreadOnServer = data?.swap?.unread_count || 0;
  useEffect(() => {
    if (!focused || !lastServerId) return;
    if (reportedRead.current === lastServerId && !unreadOnServer) return;
    reportedRead.current = lastServerId;
    apiFetch(`/api/swaps/${id}/read`, { method: "POST", body: { message_id: lastServerId } })
      .then(() => {
        qc.invalidateQueries({ queryKey: ["notifications"] });
        qc.invalidateQueries({ queryKey: ["notificationFeed"] });
        qc.invalidateQueries({ queryKey: ["swaps"], refetchType: "none" });
      })
      .catch(() => {
        reportedRead.current = null; // try again on the next poll
      });
  }, [focused, lastServerId, unreadOnServer, id, qc]);

  const act = async (fn: () => Promise<any>, hap: "light" | "success" = "light") => {
    try {
      await fn();
      haptic(hap);
      invalidateAll();
    } catch (e: any) {
      toast(e.message || t("swapChat.somethingWentWrong"), "error");
    }
  };

  const onChangeText = (v: string) => {
    textRef.current = v;
    setText(v);
  };

  /** Sends one queued message. The client_id makes a retry of the same message idempotent server-side. */
  const deliver = async (item: { client_id: string; text: string }) => {
    try {
      const res = await apiFetch<{ message: any }>(`/api/swaps/${id}/messages`, {
        method: "POST",
        body: { text: item.text, client_id: item.client_id },
      });
      qc.setQueryData<ChatData>(["swap", id], (d) => (d ? { ...d, messages: mergeMessages(d.messages, [res.message]) } : d));
      setOutbox((o) => o.filter((m) => m.client_id !== item.client_id));
      // The Swaps list shows the last message: mark it stale so it refreshes the next time it's shown,
      // without refetching it (and the notifications) after every single message.
      qc.invalidateQueries({ queryKey: ["swaps"], refetchType: "none" });
    } catch (e: any) {
      setOutbox((o) => o.map((m) => (m.client_id === item.client_id ? { ...m, status: "failed" } : m)));
      if (e?.code === "chat_locked") {
        // The chat state changed under us (e.g. the swap was declined): show the server's view.
        toast(e.message, "error");
        invalidateAll();
      }
    }
  };

  // Optimistic send: the bubble appears at once (marked as sending), the input clears at once, and the
  // request runs in the background. Only the server's response turns it into a delivered message.
  const send = () => {
    const body = textRef.current.trim();
    if (!body) return;
    textRef.current = "";
    setText("");
    haptic("light");
    nearEnd.current = true; // your own message always scrolls into view
    const item: OutboxItem = { client_id: newClientId(), text: body, status: "sending", created_at: new Date().toISOString() };
    setOutbox((o) => [...o, item]);
    deliver(item);
  };

  const retry = (item: OutboxItem) => {
    if (item.status !== "failed") return;
    setOutbox((o) => o.map((m) => (m.client_id === item.client_id ? { ...m, status: "sending" } : m)));
    deliver(item);
  };

  const listData = useMemo<any[]>(() => withOutbox(data?.messages || [], outbox, myId), [data?.messages, outbox, myId]);

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

  // Keep the latest message in view: after new content, and when the list gets shorter because the
  // keyboard opened — but only if the reader was already at the bottom (never yank them away from
  // older messages they scrolled up to read).
  const onScroll = useCallback((e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
    nearEnd.current = contentOffset.y + layoutMeasurement.height >= contentSize.height - NEAR_END_PX;
  }, []);
  const onListLayout = useCallback((e: LayoutChangeEvent) => {
    const h = e.nativeEvent.layout.height;
    const shrank = h < listHeight.current;
    listHeight.current = h;
    if (shrank && nearEnd.current) requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: false }));
  }, []);
  const onContentSizeChange = useCallback(() => {
    if (nearEnd.current) listRef.current?.scrollToEnd({ animated: true });
  }, []);

  if (isLoading || !data) {
    return (
      <View style={[styles.root, { justifyContent: "center" }]}>
        <ActivityIndicator color={colors.brandPrimary} />
      </View>
    );
  }

  const swap = data.swap;
  const other = swap.other_user;
  const otherName = displayName(t, other);
  const otherFirst = other.name?.split(" ")[0] || "";
  const status = swap.status;
  const proposal = swap.active_proposal;
  const iAmProposer = proposal && proposal.proposer_id === myId;
  const myRated = swap.is_requester ? swap.requester_rated : swap.receiver_rated;
  const iCompleted = swap.is_requester ? swap.requester_completed : swap.receiver_completed;
  const panel = chatPanel(swap, myId);

  const openProposeSheet = () => {
    setGiveId(null);
    setGetId(null);
    proposeSheet.current?.expand();
  };

  const NoteCard = swap.note ? (
    <View testID="proposal-note" style={styles.noteCard}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Quotes size={16} color={colors.brandPrimary} weight="fill" />
        <AppText variant="label" color={colors.brandPrimary}>
          {swap.is_requester ? t("swapChat.yourNote") : t("swapChat.noteFromName", { name: otherFirst })}
        </AppText>
      </View>
      <AppText variant="body" color={colors.onSurfaceSecondary}>
        {swap.note}
      </AppText>
    </View>
  ) : null;

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
              <AppText variant="caption" numberOfLines={2} style={{ textAlign: "center" }}>
                {p.offered?.title || t("swapChat.bookUnavailable")}
              </AppText>
            </View>
            <ArrowsClockwise size={20} color={colors.muted} />
            <View style={{ alignItems: "center", gap: 4, width: 70 }}>
              <BookCover uri={p.requested?.cover_url} width={54} />
              <AppText variant="caption" numberOfLines={2} style={{ textAlign: "center" }}>
                {p.requested?.title || t("swapChat.bookUnavailable")}
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
            {item.image_url ? (
              <Image
                source={{ uri: resolveImage(item.image_url) }}
                style={[styles.chatImage, { width: chatImageSize, height: chatImageSize }]}
                contentFit="cover"
                transition={200}
                accessibilityRole="image"
                accessibilityLabel={t("swapChat.photoLabel")}
              />
            ) : (
              // The sender deleted their account: their photos were deleted with it.
              <AppText variant="caption" color={colors.muted} style={{ padding: 10 }}>
                {t("swapChat.photoRemoved")}
              </AppText>
            )}
            {item.text ? (
              <AppText variant="body" color={colors.onSurface} style={{ marginTop: 6 }}>
                {item.text}
              </AppText>
            ) : null}
          </View>
        </View>
      );
    }
    const out: OutboxItem | undefined = item._outbox;
    return (
      <View style={[styles.bubbleRow, { justifyContent: mine ? "flex-end" : "flex-start" }]}>
        <Pressable
          disabled={out?.status !== "failed"}
          onPress={() => out && retry(out)}
          testID={out ? `outbox-${out.status}` : undefined}
          style={{ maxWidth: "78%", alignItems: mine ? "flex-end" : "flex-start", gap: 3 }}
        >
          <View style={[styles.bubble, { maxWidth: "100%" }, mine ? bubbleMineStyle : bubbleTheirsStyle, out?.status === "sending" && { opacity: 0.65 }, out?.status === "failed" && styles.bubbleFailed]}>
            <AppText variant="body" color={mine ? colors.onBrandPrimary : colors.onSurfaceSecondary}>
              {item.text}
            </AppText>
          </View>
          {out && (
            <AppText variant="caption" color={out.status === "failed" ? colors.error : colors.muted}>
              {out.status === "failed" ? t("swapChat.notSent") : t("swapChat.sending")}
            </AppText>
          )}
        </Pressable>
      </View>
    );
  };

  // The composer's bottom padding clears the home indicator / navigation bar while the keyboard is
  // closed. When it opens that inset is under the keyboard, so the lift is reduced by the same amount
  // (minus a small gap): the input then sits just above the keyboard with no hardcoded heights.
  const composerBottom = Math.max(insets.bottom, 10);

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.backBtn} accessibilityRole="button" accessibilityLabel={t("common.back")}>
          <DirectionalIcon>
            <ArrowLeft size={22} color={colors.onSurface} />
          </DirectionalIcon>
        </Pressable>
        <Pressable
          onPress={() => !other.is_deleted && router.push(`/person/${other.user_id}`)}
          disabled={!!other.is_deleted}
          style={{ flexDirection: "row", alignItems: "center", gap: 10, flex: 1 }}
          accessibilityRole={other.is_deleted ? undefined : "button"}
        >
          <Avatar uri={other.avatar_url} name={otherName} size={38} />
          <View style={{ flex: 1, minWidth: 0 }}>
            <AppText variant="heading" numberOfLines={1}>{otherName}</AppText>
            <AppText variant="caption" color={colors.muted}>
              {STATUS_KEYS[status] ? t(STATUS_KEYS[status]) : status}
            </AppText>
          </View>
        </Pressable>
        {!other.is_deleted && (
          <SafetyMenuButton
            userId={other.user_id}
            name={otherName}
            reportTarget={{ type: "swap", id: String(id) }}
            style={styles.backBtn}
          />
        )}
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior="padding"
        automaticOffset
        keyboardVerticalOffset={-(composerBottom - KEYBOARD_GAP)}
        enabled={panel.composer}
      >
        <FlatList
          ref={listRef}
          data={listData}
          keyExtractor={(m) => m.id}
          renderItem={renderMessage}
          ListHeaderComponent={NoteCard}
          contentContainerStyle={{ padding: 16, gap: 8, paddingBottom: 16, flexGrow: 1 }}
          onContentSizeChange={onContentSizeChange}
          onLayout={onListLayout}
          onScroll={onScroll}
          scrollEventThrottle={64}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="interactive"
          showsVerticalScrollIndicator={false}
        />

        {/* Action area */}
        <View style={[styles.actionArea, !panel.composer && { paddingBottom: insets.bottom + 12 }]}>
          {panel.waiting && (
            <View testID="chat-locked-waiting" style={{ gap: 8 }}>
              <View style={styles.lockRow}>
                <LockSimple size={16} color={colors.muted} weight="bold" />
                <AppText variant="caption" color={colors.muted} style={{ flex: 1 }}>
                  {t("swapChat.chatLockedWaiting", { name: otherFirst })}
                </AppText>
              </View>
              {panel.canPropose && (
                <Button
                  testID="propose-swap"
                  title={proposal ? t("swapChat.changeProposal") : t("swapChat.proposeSwap")}
                  variant="outline"
                  onPress={openProposeSheet}
                />
              )}
            </View>
          )}

          {panel.decide && (
            <View testID="chat-locked-decide" style={{ gap: 10 }}>
              <View style={{ gap: 2 }}>
                <AppText variant="heading">{t("swapChat.decideTitle", { name: otherFirst })}</AppText>
                <AppText variant="caption" color={colors.muted}>
                  {t("swapChat.decideBody")}
                </AppText>
              </View>
              <Pressable testID="view-requester-profile" onPress={() => router.push(`/person/${other.user_id}`)} style={styles.linkRow}>
                <UserCircle size={18} color={colors.brandPrimary} />
                <AppText variant="label" color={colors.brandPrimary}>
                  {t("swapChat.viewProfile")}
                </AppText>
              </Pressable>
              {panel.canAnswerProposal && (
                <Button
                  testID="accept-swap"
                  title={t("swapChat.acceptSwap")}
                  onPress={() => act(() => apiFetch(`/api/swaps/${id}/accept`, { method: "POST" }), "success")}
                />
              )}
              <View style={{ flexDirection: "row", gap: 10 }}>
                <Button testID="decline-swap" title={t("swapChat.decline")} variant="outline" style={{ flex: 1 }} onPress={() => act(() => apiFetch(`/api/swaps/${id}/decline`, { method: "POST" }))} />
                <Button
                  testID="accept-chat"
                  title={t("swapChat.acceptToChat")}
                  variant={panel.canAnswerProposal ? "outline" : "primary"}
                  icon={<ChatCircleDots size={18} color={panel.canAnswerProposal ? colors.brandPrimary : colors.onBrandPrimary} weight="bold" />}
                  style={{ flex: 1 }}
                  onPress={() => act(() => apiFetch(`/api/swaps/${id}/allow-chat`, { method: "POST" }), "success")}
                />
              </View>
            </View>
          )}

          {status === "pending" && panel.state === "open" && (
            <>
              {panel.canAnswerProposal ? (
                <View style={{ flexDirection: "row", gap: 10 }}>
                  <Button testID="decline-swap" title={t("swapChat.decline")} variant="outline" style={{ flex: 1 }} onPress={() => act(() => apiFetch(`/api/swaps/${id}/decline`, { method: "POST" }))} />
                  <Button testID="accept-swap" title={t("swapChat.accept")} style={{ flex: 1 }} onPress={() => act(() => apiFetch(`/api/swaps/${id}/accept`, { method: "POST" }), "success")} />
                </View>
              ) : (
                <View style={{ gap: 8 }}>
                  {iAmProposer && (
                    <AppText variant="caption" color={colors.muted} style={{ textAlign: "center" }}>
                      {t("swapChat.waitingFor", { name: otherFirst })}
                    </AppText>
                  )}
                  <Button
                    testID="propose-swap"
                    title={proposal ? t("swapChat.changeProposal") : t("swapChat.proposeSwap")}
                    variant={proposal ? "outline" : "primary"}
                    icon={!proposal ? <ArrowsClockwise size={18} color={colors.onBrandPrimary} weight="bold" /> : undefined}
                    onPress={openProposeSheet}
                  />
                </View>
              )}
            </>
          )}

          {status === "active" && (
            <View style={{ gap: 8 }}>
              {iCompleted ? (
                <AppText variant="caption" color={colors.muted} style={{ textAlign: "center" }}>
                  {t("swapChat.youMarkedComplete", { name: otherFirst })}
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
                <AppText variant="label" color={colors.brandSecondaryText}>
                  {t("swapChat.swapCompletedRated")}
                </AppText>
              </View>
            ) : (
              <Button
                testID="rate-swap"
                title={t("swapChat.rateName", { name: otherFirst })}
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

          {panel.composer && (
            <View style={[styles.inputRow, { paddingBottom: composerBottom }]}>
              <Pressable testID="attach-image" onPress={sendImage} style={styles.attachBtn} accessibilityRole="button" accessibilityLabel={t("swapChat.sendPhoto")}>
                <ImageSquare size={22} color={colors.brandPrimary} weight="regular" />
              </Pressable>
              <TextInput
                testID="message-input"
                value={text}
                onChangeText={onChangeText}
                placeholder={t("swapChat.messagePlaceholder")}
                placeholderTextColor={colors.muted}
                style={styles.input}
                multiline
                maxLength={4000}
                onFocus={() => {
                  nearEnd.current = true;
                }}
              />
              <Pressable testID="send-message" onPress={send} style={styles.sendBtn} accessibilityRole="button" accessibilityLabel={t("swapChat.send")}>
                <PaperPlaneRight size={20} color={colors.onBrandPrimary} weight="fill" />
              </Pressable>
            </View>
          )}
        </View>
      </KeyboardAvoidingView>

      {/* Propose sheet */}
      <BottomSheet ref={proposeSheet} index={-1} snapPoints={["75%"]} enablePanDownToClose backdropComponent={backdrop} backgroundStyle={{ backgroundColor: colors.surfaceSecondary }} handleIndicatorStyle={{ backgroundColor: colors.borderStrong }}>
        <BottomSheetScrollView contentContainerStyle={{ padding: 20, paddingBottom: insets.bottom + 20, gap: 14 }}>
          <AppText variant="title">{t("swapChat.proposeSwap")}</AppText>
          <AppText variant="label" color={colors.muted}>{t("swapChat.youGive")}</AppText>
          <BookPicker books={data.my_books} selectedId={giveId} onChange={setGiveId} testIDPrefix="give" emptyText={t("swapChat.addBooksFirst")} coverWidth={56} />
          <AppText variant="label" color={colors.muted}>{t("swapChat.youReceive")}</AppText>
          <BookPicker books={data.their_books} selectedId={getId} onChange={setGetId} testIDPrefix="get" emptyText={t("swapChat.theyHaveNoBooks")} coverWidth={56} />
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
        </BottomSheetScrollView>
      </BottomSheet>

      {/* Rate sheet */}
      <BottomSheet ref={rateSheet} index={-1} snapPoints={["70%"]} keyboardBehavior="interactive" keyboardBlurBehavior="restore" android_keyboardInputMode="adjustResize" enablePanDownToClose backdropComponent={backdrop} backgroundStyle={{ backgroundColor: colors.surfaceSecondary }} handleIndicatorStyle={{ backgroundColor: colors.borderStrong }}>
        <BottomSheetScrollView contentContainerStyle={{ padding: 20, paddingBottom: insets.bottom + 20, gap: 16 }} keyboardShouldPersistTaps="handled">
          <AppText variant="title">{t("swapChat.rateYourSwap")}</AppText>
          <View style={{ flexDirection: "row", justifyContent: "center", gap: 8 }}>
            {[1, 2, 3, 4, 5].map((n) => (
              <Pressable
                key={n}
                testID={`star-${n}`}
                onPress={() => { haptic("selection"); setStars(n); }}
                hitSlop={4}
                accessibilityRole="radio"
                accessibilityState={{ selected: n === stars, checked: n === stars }}
                accessibilityLabel={t("reviews.starsLabel", { count: n })}
              >
                <Star size={40} color={colors.rating} weight={n <= stars ? "fill" : "regular"} />
              </Pressable>
            ))}
          </View>
          <BottomSheetTextInput
            testID="review-input"
            value={review}
            onChangeText={setReview}
            placeholder={t("swapChat.addNoteOptional")}
            placeholderTextColor={colors.muted}
            style={styles.reviewInput}
            multiline
            maxLength={REVIEW_MAX_CHARS}
          />
          {/* Off by default: a review shows who wrote it unless the reviewer deliberately hides it. */}
          <Pressable
            testID="review-anonymous-toggle"
            accessibilityRole="switch"
            accessibilityState={{ checked: anonymous }}
            onPress={() => {
              haptic("selection");
              setAnonymous((v) => !v);
            }}
            style={styles.anonRow}
          >
            <View style={{ flex: 1, gap: 2 }}>
              <AppText variant="label">{t("reviews.postAnonymously")}</AppText>
              <AppText variant="caption" color={colors.muted}>
                {anonymous ? t("reviews.anonymousOnHint") : t("reviews.anonymousOffHint")}
              </AppText>
            </View>
            <View style={[styles.switch, anonymous && styles.switchOn]}>
              <View style={[styles.knob, anonymous && styles.knobOn]} />
            </View>
          </Pressable>
          <Button
            testID="submit-rating"
            title={t("swapChat.submitRating")}
            onPress={() =>
              act(async () => {
                await apiFetch(`/api/swaps/${id}/rate`, { method: "POST", body: rateBody(stars, review, anonymous) });
                rateSheet.current?.close();
                toast(t("swapChat.thanksForRating"), "success");
              }, "success")
            }
          />
        </BottomSheetScrollView>
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
  bubbleFailed: { opacity: 0.8, borderWidth: 1, borderColor: colors.error },
  proposalCard: { alignSelf: "center", backgroundColor: colors.surfaceSecondary, borderRadius: 16, padding: 14, borderWidth: 1, borderColor: colors.brandTertiary, marginVertical: 4 },
  // The proposal note: part of the proposal (a card at the top), deliberately unlike a chat bubble.
  noteCard: { backgroundColor: colors.brandTertiary, borderRadius: 16, padding: 14, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  lockRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 4 },
  linkRow: { flexDirection: "row", alignItems: "center", gap: 6, alignSelf: "flex-start", paddingVertical: 2 },
  actionArea: { paddingHorizontal: 16, paddingTop: 10, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border, gap: 8 },
  doneRow: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 6 },
  inputRow: { flexDirection: "row", alignItems: "flex-end", gap: 8, paddingTop: 4 },
  attachBtn: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceTertiary },
  imageBubble: { maxWidth: "72%", backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, borderRadius: 18, padding: 6 },
  chatImage: { width: 200, height: 200, borderRadius: 14, backgroundColor: colors.surfaceTertiary },
  input: { flex: 1, backgroundColor: colors.surfaceTertiary, borderRadius: 20, paddingHorizontal: 16, paddingTop: 10, paddingBottom: 10, maxHeight: 100, color: colors.onSurface, fontFamily: "DMSans-Regular", fontSize: 15 },
  sendBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  anonRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 4 },
  switch: { width: 50, height: 30, borderRadius: 15, backgroundColor: colors.surfaceTertiary, padding: 3, justifyContent: "center" },
  switchOn: { backgroundColor: colors.brandSecondary },
  knob: { width: 24, height: 24, borderRadius: 12, backgroundColor: "#FFFFFF" },
  knobOn: { alignSelf: "flex-end" },
  reviewInput: { backgroundColor: colors.surfaceTertiary, borderRadius: 12, padding: 14, minHeight: 70, color: colors.onSurface, fontFamily: "DMSans-Regular", fontSize: 15 },
}));
