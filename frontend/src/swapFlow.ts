/**
 * Pure v2.2 swap/chat/notification logic (no React/Expo imports, so it can be unit-tested with plain
 * Node). The server is the authority on all of this — these only decide what the app SHOWS.
 */

/** Same limit the backend enforces on the optional proposal note (NOTE_MAX_CHARS in server.py). */
export const NOTE_MAX_CHARS = 280;

export type ChatState = "open" | "locked_waiting" | "locked_needs_you" | "closed";

type SwapLike = {
  status: string;
  is_requester?: boolean;
  chat?: { state?: string | null; can_send?: boolean } | null;
  active_proposal?: { proposer_id?: string } | null;
};

/** The chat state the server reported. Older servers sent no `chat`: fall back to the old status rule. */
export function chatStateOf(swap: SwapLike): ChatState {
  const s = swap.chat?.state;
  if (s === "open" || s === "locked_waiting" || s === "locked_needs_you" || s === "closed") return s;
  return swap.status === "declined" || swap.status === "cancelled" ? "closed" : "open";
}

/** What the bottom of the conversation offers. */
export function chatPanel(swap: SwapLike, myId: string | undefined) {
  const state = chatStateOf(swap);
  const pending = swap.status === "pending";
  const proposal = swap.active_proposal;
  return {
    state,
    /** The message composer (text + photo). */
    composer: state === "open",
    /** Requester waiting for the recipient to accept — no composer, a waiting note instead. */
    waiting: state === "locked_waiting",
    /** Recipient deciding: accept the books, accept to chat, or decline. */
    decide: state === "locked_needs_you",
    /** Accept/Decline for a proposal the other reader made (either locked or open chat). */
    canAnswerProposal: pending && !!proposal && proposal.proposer_id !== myId,
    /** Proposing/changing books: the requester while waiting, or anyone once the chat is open. */
    canPropose: pending && (state === "open" || state === "locked_waiting"),
  };
}

export type SwapWith = {
  open: { id: string; status: string } | null;
  latest: { id: string; status: string } | null;
  /** Missing on older servers (they had no "Currently exchanging" enforcement). */
  can_propose?: boolean;
  latest_chat_open?: boolean;
};

export type SwapStartDecision =
  | { kind: "open"; swapId: string }
  | { kind: "compose" }
  | { kind: "askPrevious"; swapId: string; status: string }
  | { kind: "openPreviousOnly"; swapId: string }
  | { kind: "notExchanging" };

/**
 * "Request swap" on a profile or a book:
 *  - an open swap with this reader -> open it;
 *  - the reader is not accepting exchanges -> their previous conversation if it is still open to chat
 *    (previous partners keep chatting), otherwise a "not accepting exchanges" message;
 *  - a finished swap exists -> ask: open that conversation, or compose a new proposal;
 *  - otherwise -> compose a new proposal.
 */
export function decideSwapStart(w: SwapWith): SwapStartDecision {
  if (w.open) return { kind: "open", swapId: w.open.id };
  if (w.can_propose === false) {
    return w.latest && w.latest_chat_open ? { kind: "openPreviousOnly", swapId: w.latest.id } : { kind: "notExchanging" };
  }
  if (w.latest) return { kind: "askPrevious", swapId: w.latest.id, status: w.latest.status };
  return { kind: "compose" };
}

export type FeedItem = {
  id: string;
  kind: "event" | "message";
  type: string;
  swap_id: string;
  actor: { user_id: string; name: string; avatar_url?: string | null };
  read: boolean;
  unread_count?: number;
  created_at: string;
};

const FEED_KEYS: Record<string, string> = {
  message: "notifications.newMessages",
  proposal_received: "notifications.proposalReceived",
  proposal_accepted: "notifications.proposalAccepted",
  proposal_declined: "notifications.proposalDeclined",
  proposal_cancelled: "notifications.proposalCancelled",
  chat_accepted: "notifications.chatAccepted",
};

/** Translation key + params for one notification row; unknown future types get a generic line. */
export function feedText(item: FeedItem): { key: string; params: Record<string, unknown> } {
  const name = item.actor?.name || "";
  return {
    key: FEED_KEYS[item.type] || "notifications.generic",
    params: item.kind === "message" ? { name, count: item.unread_count || 1 } : { name },
  };
}

/** Where tapping a notification goes: always that swap's conversation (proposal, swap and chat live there). */
export function feedRoute(item: Pick<FeedItem, "swap_id">): string {
  return `/swap/${item.swap_id}`;
}

/** Bell badge: a dot while anything is unread; the number is shown only in the notification center. */
export function hasUnread(counts: { unread_total?: number } | null | undefined): boolean {
  return (counts?.unread_total || 0) > 0;
}

/** Book picker: tapping a book selects it; tapping the selected book clears the choice. */
export function togglePick(current: string | null, tapped: string): string | null {
  return current === tapped ? null : tapped;
}

/** Characters left for the note, counted the way the server counts them (after trimming). */
export function noteCharsLeft(note: string): number {
  return NOTE_MAX_CHARS - note.trim().length;
}

export type PushUiState = "off" | "on" | "onNoPush" | "denied" | "unsupported";

/**
 * What the Notifications toggle shows. It is ON only when the user opted in on the server AND the OS
 * permission is granted; `pushReady` says whether this device also got a push token (it may not, e.g.
 * a build without push credentials, or Expo Go on Android) — then in-app alerts still work.
 */
export function pushUiState(opts: {
  enabledOnServer: boolean;
  permission: "granted" | "denied" | "undetermined" | "unsupported";
  pushReady: boolean;
}): PushUiState {
  if (opts.permission === "unsupported") return "unsupported";
  if (!opts.enabledOnServer) return "off";
  if (opts.permission === "denied") return "denied";
  if (opts.permission !== "granted") return "off";
  return opts.pushReady ? "on" : "onNoPush";
}
