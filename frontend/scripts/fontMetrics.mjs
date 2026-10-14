// Text measurement for layout tests, from the app's own bundled TTF files (no native renderer).
// Reads each glyph's advance width (cmap + hmtx) and wraps words greedily, as React Native does.
// No kerning or shaping: for Arabic the isolated letter forms are measured, which are wider than the
// joined forms the phone draws, so Arabic results err on the long (safe) side.
import { readFileSync } from "node:fs";

export function loadFont(url) {
  const buf = readFileSync(url);
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const tables = {};
  for (let i = 0, n = dv.getUint16(4); i < n; i++) {
    const rec = 12 + i * 16;
    tables[String.fromCharCode(...buf.subarray(rec, rec + 4))] = dv.getUint32(rec + 8);
  }
  const unitsPerEm = dv.getUint16(tables.head + 18);
  const numHMetrics = dv.getUint16(tables.hhea + 34);
  const advance = (gid) => dv.getUint16(tables.hmtx + 4 * Math.min(gid, numHMetrics - 1));

  // Unicode cmap subtable: prefer format 12 (full Unicode), else format 4 (BMP).
  const cmap = tables.cmap;
  let f4 = null, f12 = null;
  for (let i = 0, n = dv.getUint16(cmap + 2); i < n; i++) {
    const off = cmap + dv.getUint32(cmap + 4 + i * 8 + 4);
    const fmt = dv.getUint16(off);
    if (fmt === 12) f12 = off;
    else if (fmt === 4) f4 = off;
  }
  const glyph = (cp) => {
    if (f12 !== null) {
      for (let i = 0, n = dv.getUint32(f12 + 12); i < n; i++) {
        const g = f12 + 16 + i * 12, start = dv.getUint32(g), end = dv.getUint32(g + 4);
        if (cp >= start && cp <= end) return dv.getUint32(g + 8) + (cp - start);
      }
      return 0;
    }
    const segX2 = dv.getUint16(f4 + 6), ends = f4 + 14, starts = ends + segX2 + 2;
    const deltas = starts + segX2, ranges = deltas + segX2;
    for (let i = 0; i < segX2; i += 2) {
      if (cp > dv.getUint16(ends + i)) continue;
      const start = dv.getUint16(starts + i);
      if (cp < start) return 0;
      const delta = dv.getInt16(deltas + i), ro = dv.getUint16(ranges + i);
      if (ro === 0) return (cp + delta) & 0xffff;
      const g = dv.getUint16(ranges + i + ro + 2 * (cp - start));
      return g === 0 ? 0 : (g + delta) & 0xffff;
    }
    return 0;
  };
  const cache = new Map();
  /** Width in dp of `text` at `fontSize`. Combining marks (no advance in practice) count as 0. */
  const width = (text, fontSize) => {
    let units = 0;
    for (const ch of text) {
      const cp = ch.codePointAt(0);
      if (/\p{M}/u.test(ch)) continue;
      if (!cache.has(cp)) cache.set(cp, advance(glyph(cp)));
      units += cache.get(cp);
    }
    return (units / unitsPerEm) * fontSize;
  };
  return { width };
}

/** Lines `text` wraps into within `maxWidth` dp (greedy, word by word; an over-long word gets its own line). */
export function wrapLines(font, text, fontSize, maxWidth) {
  const lines = [];
  let line = "";
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const next = line ? `${line} ${word}` : word;
    if (line && font.width(next, fontSize) > maxWidth) {
      lines.push(line);
      line = word;
    } else line = next;
  }
  if (line) lines.push(line);
  return lines;
}
