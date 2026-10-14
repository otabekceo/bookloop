"""Unit tests for search_text.py: normalization, cross-script keys, fuzzy matching, ranking and
OpenLibrary alias extraction. Pure functions, so no backend or database is needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import search_text as st  # noqa: E402
from search_text import normalize, score_book, token_keys  # noqa: E402


def book(title, author="", aliases=()):
    return {"title": title, "author": author,
            "aliases": [{"kind": k, "value": v, "source": "openlibrary", "ref": "/works/OL1W"} for k, v in aliases]}


def matches(query, b):
    return score_book(query, b) > 0


def rank(query, books):
    scored = [(score_book(query, b), i, b["title"]) for i, b in enumerate(books)]
    return [t for s, i, t in sorted(scored, key=lambda x: (-x[0], x[1])) if s > 0]


DOSTOEVSKY_CYR = book("Преступление и наказание", "Фёдор Достоевский")
DOSTOEVSKY_LAT = book("Crime and Punishment", "Fyodor Dostoevsky")
CRIME_WITH_ALIASES = book("Crime and Punishment", "Fyodor Dostoevsky", [
    ("title", "Преступление и наказание"), ("title", "Delitto e castigo"), ("title", "Jinoyat va jazo"),
    ("title", "الجريمة والعقاب"), ("author", "Фёдор Михайлович Достоевский"), ("author", "فيودور دوستويفسكي"),
])


# ------------------------------------------------------------------ normalization
def test_case_unicode_punctuation_and_apostrophes():
    assert normalize("  CRIME   &   Punishment!! ") == "crime punishment"
    assert normalize("Ｃｒｉｍｅ") == "crime"                      # full-width (NFKC)
    assert normalize("Café") == normalize("Café") == "cafe"  # combining vs precomposed accent
    assert normalize("Perché") == "perche"
    for apostrophe in "'’‘ʼʻ`":
        assert normalize(f"O{apostrophe}tkan kunlar") == "otkan kunlar"
    assert normalize("Ёлка") == normalize("Елка")
    assert normalize("") == normalize(None) == normalize("!!!") == ""


def test_arabic_orthographic_variants_unify():
    assert normalize("الأسود يليق بك") == normalize("الاسود يليق بك")
    assert normalize("مَدْرَسَة") == normalize("مدرسه")       # tashkeel removed, ة ~ ه
    assert normalize("إحسان") == normalize("احسان")
    assert normalize("ـكتـاب") == normalize("كتاب")          # tatweel
    assert normalize("١٩٨٤") == "1984"


def test_originals_are_not_rewritten():
    b = book("Преступление и наказание", "Фёдор Достоевский")
    st.book_fields(b)
    assert b["title"] == "Преступление и наказание" and b["author"] == "Фёдор Достоевский"


# ------------------------------------------------------------------ transliteration / cross-script
def test_transliteration_variants_share_one_key():
    keys = {token_keys(normalize(n))[0] for n in ("Dostoevsky", "Dostoyevsky", "Dostoevskiy", "Dostoevskii", "Достоевский")}
    assert keys == {"dostoevski"}
    assert token_keys("fyodor") == token_keys(normalize("Фёдор")) == token_keys("fedor")
    assert token_keys("tolstoy") == token_keys("толстой")


def test_cyrillic_author_found_by_latin_transliteration():
    for q in ("Dostoevsky", "Dostoyevsky", "Dostoevskiy", "dostoevskii", "Fyodor Dostoevsky", "Fedor Dostoevskij"):
        assert matches(q, DOSTOEVSKY_CYR), q


def test_latin_author_found_by_cyrillic():
    assert matches("Достоевский", DOSTOEVSKY_LAT)
    assert matches("Фёдор Достоевский", DOSTOEVSKY_LAT)
    assert matches("Толстой", book("War and Peace", "Leo Tolstoy"))


def test_uzbek_cyrillic_and_latin_meet():
    b = book("O'tkan kunlar", "Abdulla Qodiriy")
    for q in ("Ўткан кунлар", "otkan kunlar", "O‘tkan kunlar", "Абдулла Қодирий", "Qodiriy", "қодирий"):
        assert matches(q, b), q
    assert matches("Otkan kunlar", book("Ўткан кунлар", "Абдулла Қодирий"))
    assert matches("Xudoyberdi", book("Shaytanat", "Худойберди Тўхтабоев"))


# ------------------------------------------------------------------ languages
def test_english_title_works_normally():
    b = book("The Great Gatsby", "F. Scott Fitzgerald")
    for q in ("The Great Gatsby", "great gatsby", "gatsby", "Fitzgerald", "GREAT gats"):
        assert matches(q, b), q
    assert not matches("gatsby fitzgerald hemingway", b)


def test_russian_search():
    b = book("Мастер и Маргарита", "Михаил Булгаков")
    for q in ("мастер и маргарита", "Маргарита", "Булгаков", "Master i Margarita", "Bulgakov", "Mikhail Bulgakov"):
        assert matches(q, b), q


def test_italian_search():
    b = book("Il nome della rosa", "Umberto Eco")
    for q in ("nome della rosa", "Il Nome Della Rosa!", "umberto eco", "rosa"):
        assert matches(q, b), q
    assert matches("perche", book("Perché leggere i classici", "Italo Calvino"))


def test_arabic_search():
    b = book("الأسود يليق بك", "أحلام مستغانمي")
    for q in ("الاسود يليق بك", "يليق", "احلام مستغانمي", "الأسوَد"):
        assert matches(q, b), q
    assert matches("اسود", b)  # without the article


# ------------------------------------------------------------------ aliases (translated titles)
def test_translated_titles_found_through_metadata_aliases():
    for q in ("Преступление и наказание", "Delitto e castigo", "Jinoyat va jazo", "الجريمة والعقاب", "فيودور دوستويفسكي"):
        assert matches(q, CRIME_WITH_ALIASES), q


def test_no_invented_translations_without_aliases():
    # Without metadata, another language's title is NOT matched — nothing is guessed.
    for q in ("Delitto e castigo", "Jinoyat va jazo", "الجريمة والعقاب"):
        assert not matches(q, DOSTOEVSKY_LAT), q


def test_arabic_author_reachable_from_latin_only_via_alias():
    plain = book("Palace Walk", "Naguib Mahfouz")
    assert not matches("نجيب محفوظ", plain)
    assert matches("نجيب محفوظ", book("Palace Walk", "Naguib Mahfouz", [("author", "نجيب محفوظ")]))
    assert matches("Naguib Mahfuz", plain)  # deterministic Latin variants fold together (ou ~ u)
    # "Najib" vs "Naguib" is a regional transliteration choice, not a deterministic fold: only an
    # alias may bridge it, never a looser fuzzy rule.
    assert not matches("Najib Mahfuz", plain)
    assert matches("Najib Mahfuz", book("Palace Walk", "Naguib Mahfouz", [("author", "Najib Mahfuz")]))


# ------------------------------------------------------------------ fuzzy
def test_typos_are_tolerated():
    assert matches("Dostoevksy", DOSTOEVSKY_LAT)
    assert matches("punishmnet", DOSTOEVSKY_LAT)
    assert matches("Bulgakof", book("Мастер и Маргарита", "Михаил Булгаков"))
    assert matches("dostoebsk", DOSTOEVSKY_LAT)  # typo in a partly typed word


def test_fuzzy_is_not_overly_broad():
    assert not matches("cat", book("The Car", "Someone"))           # short words: exact only
    assert not matches("rosa", book("Russia", "Someone"))
    assert not matches("tolstoy", book("The Hobbit", "J. R. R. Tolkien"))
    assert not matches("dostoevsky", book("Dead Souls", "Nikolai Gogol"))
    assert not matches("crime and punishment", book("Crime Scene", "Someone"))  # every word must match


CORPUS = [
    book("Crime and Punishment", "Fyodor Dostoevsky"), book("The Idiot", "Fyodor Dostoevsky"),
    book("Братья Карамазовы", "Фёдор Достоевский"), book("War and Peace", "Leo Tolstoy"),
    book("Anna Karenina", "Лев Толстой"), book("The Hobbit", "J. R. R. Tolkien"),
    book("Dead Souls", "Nikolai Gogol"), book("Мастер и Маргарита", "Михаил Булгаков"),
    book("O'tkan kunlar", "Abdulla Qodiriy"), book("Il nome della rosa", "Umberto Eco"),
    book("Il Gattopardo", "Giuseppe Tomasi di Lampedusa"), book("الأسود يليق بك", "أحلام مستغانمي"),
    book("The Great Gatsby", "F. Scott Fitzgerald"), book("1984", "George Orwell"),
    book("Animal Farm", "George Orwell"), book("Dostoevsky: A Writer in His Time", "Joseph Frank"),
    book("Crime Scene", "Patricia Cornwell"), book("Punishment Park", "Someone Else"),
    book("Doctor Zhivago", "Boris Pasternak"), book("Do Androids Dream of Electric Sheep?", "Philip K. Dick"),
]


def test_no_false_positive_explosion():
    hits = rank("Dostoevskiy", CORPUS)
    assert set(hits) == {"Crime and Punishment", "The Idiot", "Братья Карамазовы", "Dostoevsky: A Writer in His Time"}
    assert rank("orwell", CORPUS) == ["1984", "Animal Farm"]
    assert rank("zzzz", CORPUS) == []
    assert rank("the", CORPUS) == ["The Idiot", "The Hobbit", "The Great Gatsby"]  # a lone stopword still works
    for q in ("Tolstoy", "Толстой"):
        assert set(rank(q, CORPUS)) == {"War and Peace", "Anna Karenina"}, q


# ------------------------------------------------------------------ ranking
def test_ranking_tiers():
    exact = book("Gogol", "x")
    alias = book("Other title", "y", [("title", "Gogol")])
    prefix = book("Gogolevsky Boulevard", "z")
    translit = book("Гоголь", "w")
    fuzzy = book("Gagol", "v")
    books = [fuzzy, translit, prefix, alias, exact]
    assert rank("Gogol", books) == ["Gogol", "Other title", "Gogolevsky Boulevard", "Гоголь", "Gagol"]
    s = [score_book("Gogol", b) for b in (exact, alias, prefix, translit, fuzzy)]
    assert s == sorted(s, reverse=True) and len(set(s)) == 5


def test_other_language_query_never_ranks_correct_book_below_fuzzy_noise():
    noise = [book("Delitti e castighi minori", "Someone"), book("Delitto perfetto", "Someone")]
    right = CRIME_WITH_ALIASES
    assert rank("Delitto e castigo", noise + [right])[0] == "Crime and Punishment"
    assert rank("Достоевский", [book("Dostoevsky Street", "x"), book("Dostoevskyy fan club", "y"), DOSTOEVSKY_CYR])[0] == "Преступление и наказание"


def test_exact_original_beats_exact_alias():
    original = book("Delitto e castigo", "Fëdor Dostoevskij")
    via_alias = CRIME_WITH_ALIASES
    assert rank("Delitto e castigo", [via_alias, original]) == ["Delitto e castigo", "Crime and Punishment"]


# ------------------------------------------------------------------ candidate lookup
def test_candidate_prefixes_cover_both_scripts():
    assert set(st.candidate_prefixes("Достоевский")) == {"дос", "dos"}
    assert set(st.candidate_prefixes("the Dostoevskiy")) == {"dos"}
    assert st.candidate_prefixes("*") == [] and st.candidate_prefixes(".*") == []


def test_index_tokens_cover_every_form_a_query_looks_up():
    b = CRIME_WITH_ALIASES
    toks = st.index_tokens([b["title"], b["author"], *(a["value"] for a in b["aliases"])])
    for q in ("Dostoyevsky", "Достоевский", "Delitto", "الجريمة", "jinoyat"):
        assert any(t.startswith(p) for p in st.candidate_prefixes(q) for t in toks), q


# ------------------------------------------------------------------ OpenLibrary aliases
OL_DOC = {
    "key": "/works/OL166894W", "title": "Преступление и наказание", "author_name": ["Fiódor Dostoievski"],
    "author_alternative_name": ["Fyodor Dostoyevsky", "Fedor Dostoevskiy", "Фёдор Достоевский",
                                "ドストエフスキー", "DOSTOEVSKY", "Fyodor Dostoevsky"],
}
OL_EDITIONS = [
    {"title": "Crime and Punishment", "languages": [{"key": "/languages/eng"}]},
    {"title": "Crime and Punishment", "languages": [{"key": "/languages/eng"}]},
    {"title": "Delitto e castigo", "languages": [{"key": "/languages/ita"}]},
    {"title": "Prestuplenie i nakazanie", "languages": [{"key": "/languages/rus"}]},
    {"title": "Prestuplenie i nakazanie", "languages": [{"key": "/languages/rus"}]},
    {"title": "Crime and punishment (typo edition)", "languages": [{"key": "/languages/rus"}]},
    {"title": "Schuld und Sühne", "languages": [{"key": "/languages/ger"}]},
    {"title": "罪と罰", "languages": [{"key": "/languages/jpn"}]},
]


def test_openlibrary_work_is_verified_before_use():
    titles = [e["title"] for e in OL_EDITIONS]
    assert st.same_work("Crime and Punishment", "Fyodor Dostoevsky", OL_DOC, titles)
    assert st.same_work("Преступление и наказание", "Достоевский", OL_DOC, titles)
    assert st.same_work("Crime and Punishment", "", OL_DOC, titles)
    assert not st.same_work("Crime and Punishment", "Patricia Cornwell", OL_DOC, titles)
    assert not st.same_work("Crime Scene", "Fyodor Dostoevsky", OL_DOC, titles)


def test_openlibrary_aliases_have_provenance_and_skip_noise():
    al = st.aliases_from_openlibrary("Crime and Punishment", "Fyodor Dostoevsky", OL_DOC, OL_EDITIONS)
    assert all(a["source"] == "openlibrary" and a["ref"] == "/works/OL166894W" for a in al)
    titles = {a["value"]: a.get("lang") for a in al if a["kind"] == "title"}
    assert titles == {"Преступление и наказание": None, "Delitto e castigo": "it", "Prestuplenie i nakazanie": "ru"}
    authors = [a["value"] for a in al if a["kind"] == "author"]
    assert "Фёдор Достоевский" in authors and "Fyodor Dostoyevsky" in authors
    assert "Fyodor Dostoevsky" not in authors          # same as the stored author
    assert not any("ドストエフスキー" in v for v in authors)  # outside BookLoop's scripts
    assert len({normalize(a["value"]) for a in al}) == len(al)  # no duplicates


# ------------------------------------------------------------------ typos in the first letters
def test_typo_in_first_letters_found_through_reversed_keys():
    toks = st.index_tokens(["Crime and Punishment", "Fyodor Dostoevsky"])
    hit = lambda prefixes: any(t.startswith(p) for p in prefixes for t in toks)  # noqa: E731
    for q in ("Fostoevsky", "Dpstoevsky"):
        assert not hit(st.candidate_prefixes(q)), q  # the normal lookup cannot see it...
        assert hit(st.reverse_prefixes(q)), q         # ...the ending-based fallback can
        assert matches(q, DOSTOEVSKY_LAT), q
    # In a multi-word query the longest word leads the lookup, so a typo elsewhere is just fuzzy.
    assert matches("Xrime and punishment", DOSTOEVSKY_LAT)
    # Short words never get the fallback (they are not typo-tolerant).
    assert st.reverse_prefixes("Eco") == []
    assert not any(t.startswith(st.REVERSED) for t in st.index_tokens(["Eco"]))
    # Reversed keys can never collide with stored words: the marker never survives normalization.
    assert st.normalize("~ikseveotsod") == "ikseveotsod"


def test_transliterate_gives_a_plain_latin_spelling():
    assert st.transliterate("Фёдор Достоевский") == "fedor dostoevskiy"
    assert st.transliterate("Ўткан кунлар") == "otkan kunlar"
    assert st.transliterate("Crime") == "crime"


# ------------------------------------------------------------------ Wikidata
def _ent(qid, labels, aliases=None, authors=()):
    return {"id": qid, "labels": {l: {"value": v} for l, v in labels.items()},
            "aliases": {l: [{"value": v} for v in vs] for l, vs in (aliases or {}).items()},
            "claims": {"P50": [{"mainsnak": {"datavalue": {"value": {"id": a}}}} for a in authors]}}


WD_NOVEL = _ent("Q165318", {"en": "Crime and Punishment", "ru": "Преступление и наказание", "it": "Delitto e castigo",
                            "uz": "Jinoyat va jazo", "ar": "الجريمة والعقاب", "de": "Schuld und Sühne"},
                {"it": ["Raskolnikow", "Sonya Marmeladova"]}, authors=["Q991"])
WD_GAME = _ent("Q5185252", {"en": "Crime and Punishment"})
WD_FILM = _ent("Q1978763", {"en": "Crime and Punishment", "ru": "Преступление и наказание (фильм)"})
WD_AUTHORS = {"Q991": _ent("Q991", {"en": "Fyodor Dostoyevsky", "ru": "Фёдор Михайлович Достоевский", "ar": "فيودور دوستويفسكي",
                                    "uz": "Fyodor Dostoyevskiy"}, {"en": ["Fyodor Dostoevsky", "Doestoevsky"]})}


def test_wikidata_match_needs_an_authored_work_with_matching_title_and_author():
    works = [WD_GAME, WD_NOVEL, WD_FILM]
    assert st.wikidata_match("Crime and Punishment", "Fyodor Dostoevsky", works, WD_AUTHORS)["id"] == "Q165318"
    assert st.wikidata_match("Преступление и наказание", "Достоевский", works, WD_AUTHORS)["id"] == "Q165318"
    assert st.wikidata_match("Crime and Punishment", "", works, WD_AUTHORS)["id"] == "Q165318"  # only authored work
    assert st.wikidata_match("Crime and Punishment", "Patricia Cornwell", works, WD_AUTHORS) is None
    assert st.wikidata_match("Crime and Punishment", "Fyodor Dostoevsky", [WD_GAME, WD_FILM], WD_AUTHORS) is None
    # Two authored works with that title and no stored author: ambiguous, so nothing is attached.
    other = _ent("Q9", {"en": "Crime and Punishment"}, authors=["Q10"])
    assert st.wikidata_match("Crime and Punishment", "", [WD_NOVEL, other], WD_AUTHORS) is None


def test_wikidata_aliases_use_labels_not_noisy_work_aliases():
    al = st.aliases_from_wikidata("Crime and Punishment", "Fyodor Dostoevsky", WD_NOVEL, WD_AUTHORS)
    titles = {a["lang"]: a["value"] for a in al if a["kind"] == "title"}
    assert titles == {"ru": "Преступление и наказание", "it": "Delitto e castigo", "uz": "Jinoyat va jazo", "ar": "الجريمة والعقاب"}
    values = [a["value"] for a in al]
    assert "Raskolnikow" not in values and "Schuld und Sühne" not in values
    assert {"فيودور دوستويفسكي", "Fyodor Dostoyevsky", "Doestoevsky"} <= set(values)
    assert "Fyodor Dostoevsky" not in values  # identical to the stored author
    assert all(a["source"] == "wikidata" for a in al)
    assert {a["ref"] for a in al if a["kind"] == "title"} == {"Q165318"} and {a["ref"] for a in al if a["kind"] == "author"} == {"Q991"}


def test_merge_aliases_dedupes_across_sources_first_source_wins():
    wd = [{"kind": "title", "value": "Delitto e castigo", "source": "wikidata", "ref": "Q1"}]
    ol = [{"kind": "title", "value": "DELITTO E CASTIGO", "source": "openlibrary", "ref": "/works/OL1W"},
          {"kind": "author", "value": "F. Dostoevskii", "source": "openlibrary", "ref": "/works/OL1W"}]
    merged = st.merge_aliases(wd, ol)
    assert [(a["source"], a["value"]) for a in merged] == [("wikidata", "Delitto e castigo"), ("openlibrary", "F. Dostoevskii")]
