"""Text normalization, cross-script keys and match scoring for BookLoop book search.

Pure functions only (no database, no network), so they are unit-tested directly and shared by the
API, the ingestion/backfill code and the tests. Stored titles/authors are never changed: these
functions only build a separate search representation from them.

Two forms of every word:
  * `norm`  — same script, compared exactly: NFKC + casefold, Latin diacritics and apostrophe
              variants removed, Arabic orthographic variants unified, punctuation -> spaces.
  * `keys`  — one or more Latin "skeletons" used to match across scripts and transliteration
              schemes: Cyrillic (Russian + Uzbek) transliterated, then common variants folded
              (Dostoevsky / Dostoyevsky / Dostoevskiy / Достоевский -> "dostoevski").
Arabic has no deterministic Latin form (vowels are unwritten), so Arabic words keep their
normalized Arabic as their key; Arabic <-> Latin matching comes from metadata aliases only.
"""
import re
import unicodedata
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Set, Tuple

# Bump when the representation changes: books whose stored `search_v` differs are re-indexed.
SEARCH_VERSION = 2  # 2: reversed keys (typos in a word's first letters)

_APOSTROPHES = dict.fromkeys(map(ord, "'’‘ʼʻʹʽ`´′"), None)
# Latin letters NFD does not decompose.
_LATIN_EXTRA = {"ł": "l", "ø": "o", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe", "ı": "i", "ħ": "h"}
# Arabic: tashkeel/Quranic marks and tatweel dropped, letter variants unified, digits to ASCII.
_ARABIC_MARKS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_ARABIC_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ئ": "ي", "ی": "ي", "ؤ": "و", "ة": "ه", "ک": "ك",
    **{chr(0x0660 + i): str(i) for i in range(10)}, **{chr(0x06F0 + i): str(i) for i in range(10)},
})
# Russian + Uzbek Cyrillic -> Latin (applied to `norm`, where ё is already е).
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya", "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h", "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}
# Ordered folds that merge common transliteration variants of the same name/word.
_FOLDS: List[Tuple[re.Pattern, str]] = [(re.compile(p), r) for p, r in (
    (r"shch|sch", "sh"),
    (r"dzh|zh", "j"),          # ж: Russian "zh", Uzbek "j"
    (r"kh", "h"),              # х: "kh" / "h" (Uzbek "x" handled as a variant below)
    (r"gh", "g"),
    (r"tz", "ts"),
    (r"w", "v"),
    (r"ou", "u"),
    (r"(?<=[^aeiou])yo", "e"),  # Fyodor ~ Fedor
    (r"ye", "e"),              # Dostoyevsky ~ Dostoevsky, Yevgeny ~ Evgeny
    (r"(?:iy|ij|yy|ii|yi|y)$", "i"),  # -skiy/-skij/-skii/-sky -> -ski, Tolstoy/Толстой -> tolstoi
    (r"([a-z])\1+", r"\1"),    # doubled letters
)]
_HAS_LATIN = re.compile("[a-z]")

# Words that may be missing from a query match without failing it (only when other words remain).
STOPWORDS = frozenset(
    "the a an and of in on to for by with "      # en
    "il lo la i gli le di e del della dei un una "  # it
    "и в на с о по "                              # ru
    "va bilan "                                   # uz
    "و في من".split()                             # ar
)


@lru_cache(maxsize=20000)
def normalize(text: Optional[str]) -> str:
    """Same-script normal form: lowercase, unified Unicode/apostrophes/Arabic, no punctuation."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text).casefold().translate(_APOSTROPHES)
    s = _ARABIC_MARKS.sub("", s).translate(_ARABIC_MAP)
    out = []
    for ch in s:
        if ch.isascii() or not ch.isalpha():
            out.append(ch)
        elif ch in _LATIN_EXTRA:
            out.append(_LATIN_EXTRA[ch])
        elif ch == "ё":
            out.append("е")
        else:
            base = unicodedata.normalize("NFD", ch)[0]
            # Strip accents from Latin letters only (é -> e, ş -> s); й, ў, Arabic etc. are kept.
            out.append(base if base.isascii() else ch)
    s = "".join(out)
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    return " ".join(s.split())


def tokens(text: Optional[str]) -> List[str]:
    return normalize(text).split()


@lru_cache(maxsize=50000)
def token_keys(token: str) -> Tuple[str, ...]:
    """Cross-script Latin keys of one normalized token (usually one; two when a Latin "x" could be
    Uzbek "x" = х or English "x" = кс). Tokens without Latin/Cyrillic letters (Arabic, digits)
    are their own key, plus the article-less form for Arabic "ال" words."""
    latin = "".join(_CYRILLIC.get(ch, ch) for ch in token)
    if not _HAS_LATIN.search(latin):
        if token.startswith("ال") and len(token) > 3:
            return (token, token[2:])
        return (token,)
    variants = {latin.replace("x", "h"), latin.replace("x", "ks")} if "x" in latin else {latin}
    out = []
    for v in variants:
        for pat, rep in _FOLDS:
            v = pat.sub(rep, v)
        if v and v not in out:
            out.append(v)
    return tuple(sorted(out))


# Marks a reversed key in search_tokens. Normalized text never contains it (punctuation becomes
# spaces), so a reversed key can never collide with a real word.
REVERSED = "~"
# Typo tolerance starts at 5 letters (see _max_typos), so shorter keys need no reversed form.
_REVERSED_MIN_LEN = 5


def index_tokens(texts: Iterable[Optional[str]]) -> List[str]:
    """Distinct values stored in `search_tokens` (books: title/author/aliases; users: name):
    normalized words, their cross-script keys, and each long Latin key reversed. The index on that
    array serves anchored-prefix candidate lookups; the reversed keys let a word with a typo in its
    first letters still be found through its (intact) ending."""
    seen: Set[str] = set()
    for t in texts:
        for tok in tokens(t):
            seen.add(tok)
            for k in token_keys(tok):
                seen.add(k)
                if len(k) >= _REVERSED_MIN_LEN and _HAS_LATIN.search(k):
                    seen.add(REVERSED + k[::-1])
    return sorted(seen)


def transliterate(text: Optional[str]) -> str:
    """Readable Latin form of a (Russian/Uzbek) Cyrillic text, for querying sources that index Latin
    spellings. Unlike `token_keys` no variants are folded, so the result is a plain spelling."""
    return "".join(_CYRILLIC.get(ch, ch) for ch in normalize(text))


def _max_typos(n: int) -> int:
    # Short words must match exactly: one typo in a 4-letter word already makes it another word.
    return 0 if n < 5 else 1 if n <= 8 else 2


def edit_distance(a: str, b: str, limit: int) -> int:
    """Optimal string alignment distance (adjacent swaps count as one), stopping early past `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: List[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


# Match strengths (higher is better). A book's score is its WEAKEST required query word, so one
# fuzzy word can never lift a book above books whose every word matched exactly.
EXACT_ORIGINAL = 100   # word of the stored title/author, same script
EXACT_ALIAS = 90       # word of a metadata alias (translated title, author spelling), same script
PREFIX = 80            # query word is the start of a title/author/alias word (typing in progress)
TRANSLIT = 70          # same word in another script/transliteration (key equality)
TRANSLIT_PREFIX = 60
FUZZY = 45             # within the typo budget
FUZZY_PREFIX = 35      # typo in a partly typed word
PHRASE_BONUS = {"original": 15, "alias": 10}  # whole query equals a whole title/author


class Field:
    __slots__ = ("norm", "toks", "keys", "origin")

    def __init__(self, text: str, origin: str):
        self.norm = normalize(text)
        self.toks = self.norm.split()
        self.keys = [token_keys(t) for t in self.toks]
        self.origin = origin  # "original" | "alias"


def book_fields(book: dict) -> List[Field]:
    out = [Field(book.get("title") or "", "original"), Field(book.get("author") or "", "original")]
    out += [Field(a.get("value") or "", "alias") for a in book.get("aliases") or []]
    return [f for f in out if f.toks]


def _word_score(q: str, qkeys: Tuple[str, ...], fields: List[Field]) -> int:
    best = 0
    for f in fields:
        for tok, keys in zip(f.toks, f.keys):
            if tok == q:
                if f.origin == "original":
                    return EXACT_ORIGINAL
                best = max(best, EXACT_ALIAS)
                continue
            if tok.startswith(q):
                best = max(best, PREFIX)
                continue
            if best >= TRANSLIT:
                continue
            for qk in qkeys:
                for k in keys:
                    if k == qk:
                        best = max(best, TRANSLIT)
                    elif k.startswith(qk) and len(qk) >= 3:
                        best = max(best, TRANSLIT_PREFIX)
                    elif best < FUZZY and _HAS_LATIN.search(qk):
                        budget = _max_typos(len(qk))
                        if budget and edit_distance(qk, k, budget) <= budget:
                            best = max(best, FUZZY)
                        elif budget and len(k) > len(qk) and edit_distance(qk, k[: len(qk)], 1) <= 1:
                            best = max(best, FUZZY_PREFIX)
    return best


def score_book(query: str, book: dict, fields: Optional[List[Field]] = None) -> int:
    """Relevance of `book` for `query` (0 = not a match). Every non-stopword query word must match
    some word of the title, author or an alias; the score is the weakest of those matches, plus a
    bonus when the whole query equals a whole title/author (original > alias)."""
    qn = normalize(query)
    words = qn.split()
    if not words:
        return 0
    required = [w for w in words if w not in STOPWORDS] or words
    fields = fields if fields is not None else book_fields(book)
    weakest = None
    for w in required:
        s = _word_score(w, token_keys(w), fields)
        if not s:
            return 0
        weakest = s if weakest is None else min(weakest, s)
    bonus = max((PHRASE_BONUS[f.origin] for f in fields if f.norm == qn), default=0)
    return weakest + bonus


def candidate_prefixes(query: str) -> List[str]:
    """Anchored prefixes for the indexed candidate lookup: the first 3 characters of the longest
    required query word, in its normalized form and each cross-script key. A match must contain
    that word (or a typo of it sharing its first 3 letters), so this never drops a real match
    except typos inside the first 3 letters."""
    words = normalize(query).split()
    required = [w for w in words if w not in STOPWORDS] or words
    if not required:
        return []
    w = max(required, key=len)
    forms = {w, *token_keys(w)}
    return sorted({f[:3] for f in forms if f})


def reverse_prefixes(query: str) -> List[str]:
    """Fallback lookup when `candidate_prefixes` finds nothing: the last 3 letters (reversed-key
    form) of the longest required word, for a typo inside its first 3 letters ("Fostoevsky").
    Only for words long enough to be typo-tolerant."""
    words = normalize(query).split()
    required = [w for w in words if w not in STOPWORDS] or words
    if not required:
        return []
    w = max(required, key=len)
    keys = [k for k in token_keys(w) if len(k) >= _REVERSED_MIN_LEN and _HAS_LATIN.search(k)]
    return sorted({REVERSED + k[::-1][:3] for k in keys})


# --------------------------------------------------------------------------- aliases from metadata
_SUPPORTED_SCRIPT = re.compile(r"^[\s\d\W_a-zA-ZÀ-ɏЀ-ӿ؀-ۿ]+$")
MAX_TITLE_ALIASES = 12
MAX_AUTHOR_ALIASES = 40
# OpenLibrary edition language codes of BookLoop's five languages.
OL_LANGS = {"eng": "en", "uzb": "uz", "rus": "ru", "ita": "it", "ara": "ar"}


def _usable(value: Optional[str]) -> bool:
    """A metadata string worth indexing: in a BookLoop script (Latin/Cyrillic/Arabic) and short."""
    v = (value or "").strip()
    return 0 < len(v) <= 200 and bool(_SUPPORTED_SCRIPT.match(v)) and bool(normalize(v))


def same_work(stored_title: str, stored_author: str, doc: dict, edition_titles: Iterable[str] = ()) -> bool:
    """Whether an OpenLibrary work is the user's book: the stored title must equal (after
    normalization/cross-script keys) the work title or one of its edition titles, and the stored
    author (when given) must equal one of the work's author names. Never a fuzzy guess."""
    def key(s):
        return " ".join(token_keys(t)[0] for t in tokens(s))

    titles = {key(doc.get("title")), *(key(t) for t in edition_titles)}
    if not key(stored_title) or key(stored_title) not in titles:
        return False
    author_words = [w for w in key(stored_author).split() if len(w) > 2]  # initials ("F. M.") skipped
    if not author_words:
        return True
    # The stored author's longest word (normally the surname) must be a word of one of the work's
    # author names: "Fyodor Dostoevsky" ~ "Dostoevsky, Fyodor" ~ "F. M. Dostoevsky".
    surname = max(author_words, key=len)
    names = [*(doc.get("author_name") or []), *(doc.get("author_alternative_name") or [])]
    return any(surname in key(n).split() for n in names)


def aliases_from_openlibrary(stored_title: str, stored_author: str, doc: dict, editions: List[dict]) -> List[Dict[str, str]]:
    """Aliases for a verified OpenLibrary work: its own title, the most common edition title in
    each BookLoop language (seen in at least two editions, or the only edition in that language),
    and its author names/spellings. Values identical (normalized) to the stored ones are skipped."""
    ref = doc.get("key") or ""
    have = {normalize(stored_title), normalize(stored_author)}
    out: List[Dict[str, str]] = []

    def add(kind, value, lang=None):
        n = normalize(value)
        if not _usable(value) or n in have:
            return
        have.add(n)
        a = {"kind": kind, "value": value.strip(), "source": "openlibrary", "ref": ref}
        if lang:
            a["lang"] = lang
        out.append(a)

    add("title", doc.get("title") or "")
    per_lang: Dict[str, Dict[str, int]] = {}
    for e in editions:
        t = (e.get("title") or "").strip()
        for l in e.get("languages") or []:
            code = OL_LANGS.get((l.get("key") or "").rsplit("/", 1)[-1])
            if code and t:
                per_lang.setdefault(code, {})
                per_lang[code][t] = per_lang[code].get(t, 0) + 1
    for code, counts in per_lang.items():
        title, n = max(counts.items(), key=lambda kv: kv[1])
        if n >= 2 or len(counts) == 1:
            add("title", title, code)
    titles = [a for a in out if a["kind"] == "title"][:MAX_TITLE_ALIASES]
    for name in (doc.get("author_name") or []) + (doc.get("author_alternative_name") or []):
        if sum(a["kind"] == "author" for a in out) >= MAX_AUTHOR_ALIASES:
            break
        add("author", name)
    return titles + [a for a in out if a["kind"] == "author"]


# --------------------------------------------------------------------------- Wikidata
# BookLoop language -> Wikidata language code; label/alias lookups ask only for these.
WIKIDATA_LANGS = ("en", "ru", "uz", "it", "ar")
BOOK_LANGUAGE_CODES = {"English": "en", "Russian": "ru", "Uzbek": "uz", "Italian": "it", "Arabic": "ar"}


def _wd_strings(entity: dict, with_aliases: bool) -> List[Tuple[str, str]]:
    """(lang, value) of an entity's labels (and aliases) in BookLoop's languages."""
    out = [(l, v.get("value") or "") for l, v in (entity.get("labels") or {}).items() if l in WIKIDATA_LANGS]
    if with_aliases:
        for l, vs in (entity.get("aliases") or {}).items():
            if l in WIKIDATA_LANGS:
                out += [(l, v.get("value") or "") for v in vs]
    return [(l, v) for l, v in out if v]


def wikidata_author_ids(entity: dict) -> List[str]:
    """Author (P50) ids of a work entity; empty for anything that is not an authored written work
    (films, games, people…), which is what keeps same-named items out."""
    ids = []
    for c in (entity.get("claims") or {}).get("P50") or []:
        v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
        if isinstance(v, dict) and v.get("id"):
            ids.append(v["id"])
    return ids


def _key(s: str) -> str:
    return " ".join(token_keys(t)[0] for t in tokens(s))


def wikidata_match(stored_title: str, stored_author: str, works: List[dict], authors: Dict[str, dict]) -> Optional[dict]:
    """The one work entity that is verifiably the user's book, or None. A work qualifies when it
    has an author (P50), one of its labels/aliases equals the stored title (normalized, any
    script), and — when an author is stored — the stored author's longest word (the surname) is a
    word of one of its authors' names. With no stored author, a title match must be unambiguous."""
    title_key = _key(stored_title)
    if not title_key:
        return None
    author_words = [w for w in _key(stored_author).split() if len(w) > 2]
    surname = max(author_words, key=len) if author_words else None
    hits = []
    for w in works:
        aids = wikidata_author_ids(w)
        if not aids or title_key not in {_key(v) for _, v in _wd_strings(w, True)}:
            continue
        if surname is None or any(
            surname in _key(v).split() for a in aids for _, v in _wd_strings(authors.get(a) or {}, True)
        ):
            hits.append(w)
    if surname is None and len(hits) != 1:
        return None
    return hits[0] if hits else None


def aliases_from_wikidata(stored_title: str, stored_author: str, work: dict, authors: Dict[str, dict]) -> List[Dict[str, str]]:
    """The work's label in each BookLoop language (labels are the curated per-language title;
    work aliases are skipped — they mix in character names and other noise), plus its authors'
    labels and aliases (spelling variants of the name). Provenance: source "wikidata", ref = item id."""
    have = {normalize(stored_title), normalize(stored_author)}
    out: List[Dict[str, str]] = []

    def add(kind, value, lang, ref):
        n = normalize(value)
        if not _usable(value) or n in have:
            return
        have.add(n)
        out.append({"kind": kind, "value": value.strip(), "source": "wikidata", "ref": ref, "lang": lang})

    for lang, v in _wd_strings(work, False):
        add("title", v, lang, work.get("id") or "")
    for aid in wikidata_author_ids(work):
        for lang, v in _wd_strings(authors.get(aid) or {}, True)[:MAX_AUTHOR_ALIASES]:
            add("author", v, lang, aid)
    return out


def merge_aliases(*groups: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Aliases from several sources, first source winning on duplicates (by normalized value)."""
    seen: Set[Tuple[str, str]] = set()
    out = []
    for g in groups:
        for a in g:
            k = (a["kind"], normalize(a["value"]))
            if k not in seen:
                seen.add(k)
                out.append(a)
    return out
