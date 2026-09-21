# BookLoop — Product Requirements & Status

## Problem statement
BookLoop is a **local, people-first book-exchange social marketplace** ("Read. Swap. Repeat.") for a dense
local community (initial target: Messina university students and young readers). It is NOT a bookstore or a
selling app. Core loop:
Discover people → explore their books → request a general swap → chat → propose specific books → accept →
meet locally → both confirm completion → rate → repeat.

## Architecture
- **Frontend** (`frontend/`): Expo (React Native) + expo-router, @tanstack/react-query, @gorhom/bottom-sheet,
  react-native-keyboard-controller, phosphor-react-native icons, Fraunces + DM Sans (+ Noto Sans Arabic) fonts.
  Warm-editorial theme in `src/theme.ts`; design tokens are documented in `docs/design_guidelines.json`.
  The API address comes from `EXPO_PUBLIC_BACKEND_URL` (`frontend/.env`).
- **Backend** (`backend/server.py`): FastAPI + MongoDB (motor). One file, ~1,700 lines.
- **Auth:** opaque session tokens stored in `user_sessions` (7-day expiry, TTL index).
  - Email/password (bcrypt; passwords are limited to 72 bytes).
  - Google sign-in through our own Google Cloud OAuth "Web application" client: the backend runs the
    authorization-code flow (`/api/auth/google/start` → Google → `/api/auth/google/callback`) and hands the app a
    one-time `session_id` that `/api/auth/session` exchanges for a normal session token.
    Without `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` the button is hidden (`/api/auth/providers`).
- **File storage:** uploaded images live on local disk under `STORAGE_DIR` (default `backend/uploads`). Images are
  served from `/api/files/...` only with a signed, expiring link (`?exp=&sig=`) or a session token; the backend signs
  every internal file URL in its JSON responses.
- **Navigation:** 5 custom bottom tabs (Discover, Map, My Books, Swaps, Profile) + a floating "+ Add Book" button.
- **Setup and settings:** see `README.md` and `backend/.env.example`.

## Data model (MongoDB)
`users`, `user_sessions`, `books`, `swaps`, `messages`, `ratings`, `uploads`, plus short-lived `oauth_states`,
`google_logins` and `app_settings` (file-signing key). Deletes are soft (`deleted_at`).

## User personas
1. **Reader with unused books** — lists books, discovers nearby readers, swaps locally.
2. **Discoverer** — browses people-first, requests swaps without knowing exactly which book they want.
3. **Erasmus / student** — wants a local, trustworthy, visual community with ratings.

## Features
- Auth (email/password + Google), profiles, "Currently exchanging" toggle, invite link.
- Book inventory (cover photo, title, author, condition, language, genre, ISBN; status Available/Reserved/Swapped);
  quick add by search (Open Library, proxied by the backend), barcode scan, or manually.
- Discover people and books with distance / genre / language / exchanging filters; wishlist of soft preferences.
- Neighbourhood-level density map (Leaflet/OpenStreetMap; web iframe, native WebView).
- **Swap flow:** `pending` → `active` → `completed` (or `declined` / `cancelled`).
  - Only the other participant can accept a proposal; both books must be Available and get Reserved atomically.
  - Both participants must confirm completion; books become Swapped and each side's swap count increases once.
  - Each participant can rate once; ratings recompute the profile rating. Badges unlock at 1/3/5/10 swaps.
- **Chat** inside each swap: text, photos, proposal cards and system messages (4-second polling).
- Ratings and reviews on profiles.

## Privacy and security model
- Other readers never receive a user's email or exact coordinates: only neighbourhood and a computed distance.
- Every swap action is limited to the two participants and to valid states (409 otherwise).
- Books held by an active swap cannot be edited to another status or deleted.
- Uploads: 8 MB limit, JPEG/PNG/WebP/HEIC verified by content, stored with server-chosen names.
- Search input is matched literally (no regex injection).
- Google return addresses are checked against an allow-list (`APP_URL_SCHEMES`, `APP_REDIRECT_ORIGINS`, localhost).

## Internationalisation
- 5 languages: O'zbek (`uz`), English (`en`, default/fallback), Русский (`ru`), Italiano (`it`), العربية (`ar`).
- `i18next` + `react-i18next`; **348 keys per locale**, kept in parity (checked by a backend test).
- First-run language picker before sign-in/sign-up; the choice is stored on the device (AsyncStorage key
  `bookloop_language`) and on the user's profile (`preferred_language`, saved at sign-up and after sign-in).
  Profile → Language changes it later.
- Arabic is right-to-left (`I18nManager`, directional icons, bundled Noto Sans Arabic); switching direction needs an
  app reload. Dates and numbers use per-language locale formatting.
- Chat system messages are stored as an i18n key + parameters (with the English text as fallback) and rendered in
  the reader's language. Badge names are translated on the client.

## Known limitations / open issues
- Completing a swap is not a single transaction (badge, count and book updates follow the atomic status change).
- A book can appear in several pending proposals; exclusivity is enforced when a swap is accepted.
- No rate limiting, and the only password rule is the 72-byte maximum.
- Genres, conditions and book languages are English strings used as identifiers, so they are not localised.
- Chat uses polling: no push notifications, unread counts or pagination (500-message cap).
- The map and default location are hard-coded to Messina (six fixed neighbourhoods).
- Uploaded images live only on the server's disk: back up `STORAGE_DIR` with the database. No per-user quota or
  clean-up of unused files.
- Barcode scanning and camera capture need a real device.
- Real Google sign-in has only been exercised against a fake Google in tests; the frontend has no automated tests.

## Backlog
- **P1:** push notifications; real GPS permission + native map for production builds; pull-to-refresh polish.
- **P1:** hosting (backend + MongoDB), https, locked-down CORS, store builds with the `com.otabekceo.bookloop` id.
- **P2:** AI "people you may enjoy swapping with", multi-person swap chains, university/phone verification badges,
  premium/promoted listings.

## Tests
`backend/tests/test_bookloop_api.py` — HTTP tests against a running backend (see `README.md`). The tests create their own
throwaway users, plus a shared test account `test@bookloop.com` / `test1234` that they register on first use
— run them against a disposable database only.
