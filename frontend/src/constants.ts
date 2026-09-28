export const GENRES = [
  "Fiction",
  "Psychology",
  "Business",
  "History",
  "Biography",
  "Self-development",
  "Romance",
  "Fantasy",
  "Philosophy",
  "Science",
];

// Reading/book languages offered in pickers: exactly BookLoop's five supported languages. Stored as
// English names (the API matches on them); shown translated via enumLabel(t, "bookLanguage", …).
// Values saved before this list changed (e.g. "Spanish") still display and can be deselected.
export const LANGUAGES = [
  "Uzbek",
  "English",
  "Russian",
  "Italian",
  "Arabic",
];

export const CONDITIONS = ["Like New", "Good", "Acceptable"];

export const DISTANCES = [1, 5, 10, 25, 50];

export const BOOK_STATUSES = ["Available", "Reserved", "Swapped"];

export const READING_INTERESTS = [
  "Short reads",
  "Classics",
  "Bestsellers",
  "University texts",
  "Italian authors",
  "Book club picks",
  "Thrillers",
  "Poetry",
  "Graphic novels",
  "Non-fiction deep dives",
];
