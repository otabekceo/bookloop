import { useRef, useState, useCallback } from "react";
import { View, FlatList, Pressable, TextInput, ActivityIndicator, ScrollView } from "react-native";
import { useLocalSearchParams, useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAvoidingView } from "react-native-keyboard-controller";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import BottomSheet, { BottomSheetBackdrop, BottomSheetView } from "@gorhom/bottom-sheet";
import { ArrowLeft, PaperPlaneRight, ArrowsClockwise, Star, CheckCircle, X } from "phosphor-react-native";

import { AppText, Avatar, Button, BookCover, Stars, haptic, useToast } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { useAuth } from "@/src/auth";
import { makeStyles, useTheme } from "@/src/theme";

export default function SwapChat() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const styles = useStyles();
  const { colors } = useTheme();
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
      toast(e.message || "Something went wrong", "error");
    }
  };

  const send = async () => {
    const t = text.trim();
    if (!t) return;
    setText("");
    await act(() => apiFetch(`/api/swaps/${id}/messages`, { method: "POST", body: { text: t } }));
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
            {item.text}
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
              Swap proposal
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
    return (
      <View style={[styles.bubbleRow, { justifyContent: mine ? "flex-end" : "flex-start" }]}>
        <View style={[styles.bubble, mine ? styles.bubbleMine : styles.bubbleTheirs]}>
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
          <ArrowLeft size={22} color={colors.onSurface} />
        </Pressable>
        <Pressable onPress={() => router.push(`/person/${other.user_id}`)} style={{ flexDirection: "row", alignItems: "center", gap: 10, flex: 1 }}>
          <Avatar uri={other.avatar_url} name={other.name} size={38} />
          <View>
            <AppText variant="heading">{other.name}</AppText>
            <AppText variant="caption" color={colors.muted}>
              {status}
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
                  <Button testID="decline-swap" title="Decline" variant="outline" style={{ flex: 1 }} onPress={() => act(() => apiFetch(`/api/swaps/${id}/decline`, { method: "POST" }))} />
                  <Button testID="accept-swap" title="Accept" style={{ flex: 1 }} onPress={() => act(() => apiFetch(`/api/swaps/${id}/accept`, { method: "POST" }), "success")} />
                </View>
              ) : (
                <View style={{ gap: 8 }}>
                  {iAmProposer && (
                    <AppText variant="caption" color={colors.muted} style={{ textAlign: "center" }}>
                      Waiting for {other.name?.split(" ")[0]} to respond
                    </AppText>
                  )}
                  <Button
                    testID="propose-swap"
                    title={proposal ? "Change proposal" : "Propose a swap"}
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
                  You marked it complete — waiting for {other.name?.split(" ")[0]}
                </AppText>
              ) : null}
              <Button
                testID="complete-swap"
                title="Mark as completed"
                icon={<CheckCircle size={18} color={colors.onBrandPrimary} weight="bold" />}
                disabled={iCompleted}
                onPress={() => act(() => apiFetch(`/api/swaps/${id}/complete`, { method: "POST" }), "success")}
              />
            </View>
          )}

          {status === "completed" && (
            myRated ? (
              <View style={styles.doneRow}>
                <CheckCircle size={18} color={colors.brandSecondary} weight="fill" />
                <AppText variant="label" color={colors.brandSecondary}>
                  Swap completed & rated
                </AppText>
              </View>
            ) : (
              <Button
                testID="rate-swap"
                title={`Rate ${other.name?.split(" ")[0]}`}
                icon={<Star size={18} color={colors.onBrandPrimary} weight="fill" />}
                onPress={() => rateSheet.current?.expand()}
              />
            )
          )}

          {(status === "declined" || status === "cancelled") && (
            <AppText variant="label" color={colors.muted} style={{ textAlign: "center" }}>
              This swap was {status}.
            </AppText>
          )}

          {showInput && (
            <View style={[styles.inputRow, { paddingBottom: insets.bottom > 0 ? insets.bottom : 10 }]}>
              <TextInput
                testID="message-input"
                value={text}
                onChangeText={setText}
                placeholder="Message…"
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
          <AppText variant="title">Propose a swap</AppText>
          <AppText variant="label" color={colors.muted}>You give</AppText>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 10 }}>
            {data.my_books.length === 0 && <AppText variant="body" color={colors.muted}>Add books to your shelf first.</AppText>}
            {data.my_books.map((b: any) => (
              <Pressable key={b.id} testID={`give-${b.id}`} onPress={() => setGiveId(b.id)} style={[styles.pick, giveId === b.id && styles.pickOn]}>
                <BookCover uri={b.cover_url} width={56} />
                <AppText variant="caption" numberOfLines={1} style={{ width: 56 }}>{b.title}</AppText>
              </Pressable>
            ))}
          </ScrollView>
          <AppText variant="label" color={colors.muted}>You receive</AppText>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 10 }}>
            {data.their_books.length === 0 && <AppText variant="body" color={colors.muted}>They have no available books.</AppText>}
            {data.their_books.map((b: any) => (
              <Pressable key={b.id} testID={`get-${b.id}`} onPress={() => setGetId(b.id)} style={[styles.pick, getId === b.id && styles.pickOn]}>
                <BookCover uri={b.cover_url} width={56} />
                <AppText variant="caption" numberOfLines={1} style={{ width: 56 }}>{b.title}</AppText>
              </Pressable>
            ))}
          </ScrollView>
          <Button
            testID="send-proposal"
            title="Send proposal"
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
          <AppText variant="title">Rate your swap</AppText>
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
            placeholder="Add a note (optional)"
            placeholderTextColor={colors.muted}
            style={styles.reviewInput}
            multiline
          />
          <Button
            testID="submit-rating"
            title="Submit rating"
            onPress={() =>
              act(async () => {
                await apiFetch(`/api/swaps/${id}/rate`, { method: "POST", body: { stars, review } });
                rateSheet.current?.close();
                toast("Thanks for rating!", "success");
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
  input: { flex: 1, backgroundColor: colors.surfaceTertiary, borderRadius: 20, paddingHorizontal: 16, paddingTop: 10, paddingBottom: 10, maxHeight: 100, color: colors.onSurface, fontFamily: "DMSans-Regular", fontSize: 15 },
  sendBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  pick: { alignItems: "center", gap: 4, padding: 6, borderRadius: 12, borderWidth: 2, borderColor: "transparent" },
  pickOn: { borderColor: colors.brandPrimary, backgroundColor: colors.brandTertiary },
  reviewInput: { backgroundColor: colors.surfaceTertiary, borderRadius: 12, padding: 14, minHeight: 70, color: colors.onSurface, fontFamily: "DMSans-Regular", fontSize: 15 },
}));
