import { Alert, Platform } from "react-native";

import { apiFetch } from "@/src/api";

type Translate = (key: string, options?: Record<string, unknown>) => string;
type SwapRef = { id: string; status: string };

const STATUS_KEYS: Record<string, string> = {
  completed: "swaps.statusCompleted",
  declined: "swaps.statusDeclined",
  cancelled: "swaps.statusCancelled",
};

/**
 * "Request swap" from a profile or a book.
 *
 * - An open swap with this reader (pending / active) -> open that conversation.
 * - Only finished swaps (completed / declined / cancelled) -> ask: open the previous conversation, or
 *   really start a NEW request. Before, the button silently created a new pending request every time
 *   it was tapped after a swap finished (people use it to get back to the chat), which then showed
 *   up in the other reader's Incoming list next to the completed swap — looking like one swap in two
 *   places.
 * - No swap yet -> create the request.
 */
export async function openOrRequestSwap(opts: {
  userId: string;
  name: string;
  t: Translate;
  open: (swapId: string) => void;
  onError: (message: string) => void;
}): Promise<void> {
  const { userId, name, t, open, onError } = opts;
  const create = async () => {
    try {
      const res = await apiFetch<{ swap: SwapRef }>("/api/swaps", { method: "POST", body: { receiver_id: userId } });
      open(res.swap.id);
    } catch (e: any) {
      onError(e.message);
    }
  };

  const w = await apiFetch<{ open: SwapRef | null; latest: SwapRef | null }>(`/api/swaps/with/${encodeURIComponent(userId)}`);
  if (w.open) {
    open(w.open.id);
    return;
  }
  if (!w.latest) {
    await create();
    return;
  }
  const previous = w.latest;
  const status = STATUS_KEYS[previous.status] ? t(STATUS_KEYS[previous.status]) : previous.status;
  const body = t("swaps.previousBody", { name, status });
  if (Platform.OS === "web") {
    // react-native-web has no multi-button Alert.
    if (window.confirm(`${body}\n\n${t("swaps.newRequest")}?`)) await create();
    else open(previous.id);
    return;
  }
  Alert.alert(t("swaps.previousTitle", { name }), body, [
    { text: t("common.cancel"), style: "cancel" },
    { text: t("swaps.openPrevious"), onPress: () => open(previous.id) },
    { text: t("swaps.newRequest"), onPress: () => void create() },
  ]);
}
