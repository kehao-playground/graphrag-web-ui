// Locale-aware date rendering (i18n spec §5.3): every timestamp the UI
// shows goes through these, never through a raw ISO string or a slice of
// one. `lang` is i18n.language, so a language switch re-renders them.

// graphrag writes creation_date as "2026-09-21 00:18:35 +0000"; the API's
// own timestamps are ISO 8601. Both parse; anything else is null.
export function parseDateTime(value: string): Date | null {
  const iso = value.replace(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}(?:\.\d+)?) ?([+-]\d{2})(\d{2})$/, "$1T$2$3:$4");
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

const format = (value: string, lang: string, options: Intl.DateTimeFormatOptions) => {
  const d = parseDateTime(value);
  return d === null ? value : new Intl.DateTimeFormat(lang, options).format(d);
};

// Full date and time to the second: tables, drawers, version history.
export const formatDateTime = (value: string, lang: string) =>
  format(value, lang, { dateStyle: "medium", timeStyle: "medium" });

// Month, day and minute: narrow headers where the year is noise but two
// runs on the same day must still differ (matrix and diff labels).
export const formatShortDateTime = (value: string, lang: string) =>
  format(value, lang, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
