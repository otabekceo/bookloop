import i18n from "./index";

/** A server-provided message: an i18n key + params, with the English text as a fallback. */
export type KeyedText = {
  key?: string | null;
  params?: Record<string, any> | null;
  text?: string | null;
};

type Translate = (key: string, options?: any) => any;

/** Localized badge name; the server's English label is the fallback for unknown ids. */
export function badgeLabel(t: Translate, b: { id: string; label: string }): string {
  return String(t(`badges.names.${b.id}`, { defaultValue: b.label }));
}

/**
 * Renders a chat/system message in the active UI language.
 *
 * The backend stores `key` + `params` for system messages (see `_add_message` in server.py).
 * Messages written before keys existed, and keys this build doesn't know, fall back to the
 * stored English `text` so nothing ever renders blank.
 */
export function renderKeyed(t: Translate, m: KeyedText): string {
  if (m.key && i18n.exists(m.key)) {
    const params: Record<string, any> = { ...(m.params || {}) };
    // Badges travel as ids; their display names are translated here.
    if (typeof params.badge === "string" && i18n.exists(`badges.names.${params.badge}`)) {
      params.badge = t(`badges.names.${params.badge}`);
    }
    return String(t(m.key, params));
  }
  return m.text || "";
}
