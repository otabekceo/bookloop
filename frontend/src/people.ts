type Translate = (key: string, options?: Record<string, unknown>) => string;

/**
 * How another reader is named on screen. A deleted account keeps appearing in the other person's
 * swap history, but the server no longer has (or sends) its name: it is shown as "Deleted reader".
 */
export function displayName(t: Translate, person: { name?: string | null; is_deleted?: boolean } | null | undefined): string {
  if (!person || person.is_deleted || !person.name) return t("common.deletedReader");
  return person.name;
}
