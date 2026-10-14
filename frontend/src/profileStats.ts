// Layout of the Profile stats row (rating · swaps · books), kept free of React/Expo imports so the
// label widths can be unit-tested against the real fonts (scripts/logic.test.mjs).
//
// The row is three equal cells. Every cell is top-aligned and its value sits in a box of the same
// height, so the numbers stay on one line however many lines a translated label wraps into; labels
// are centered and wrap freely (never truncated, never shrunk), so long translations and large
// system font sizes just make the row taller.

/** Horizontal margin of the stats card on each side of the screen. */
export const STATS_MARGIN = 20;
/** Inner horizontal padding of each cell, so a label never touches the dividers. */
export const STAT_CELL_PADDING = 6;
export const STAT_DIVIDER_WIDTH = 1;
/** Height of the value line (the `heading` text variant's line height, at font scale 1). */
export const STAT_VALUE_HEIGHT = 22;
/** Caption variant used by the labels. */
export const STAT_LABEL_FONT_SIZE = 12;

/** Width in dp available to one stat label on a screen `screenWidth` dp wide. */
export function statLabelWidth(screenWidth: number): number {
  const cell = (screenWidth - 2 * STATS_MARGIN - 2 * STAT_DIVIDER_WIDTH) / 3;
  return cell - 2 * STAT_CELL_PADDING;
}
