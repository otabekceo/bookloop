# BookLoop — Product Requirements & Build Log

## Original problem statement
BookLoop is a **local, people-first book-exchange social marketplace** ("Read. Swap. Repeat.") for a dense local community (initial target: Messina university students & young readers). It is NOT a bookstore or selling app. Core loop:
Discover people → explore their books → request a general swap → chat → propose specific books → accept → meet locally → both confirm completion → rate → repeat.

## Architecture
- **Frontend:** Expo (React Native) + expo-router, @tanstack/react-query, @gorhom/bottom-sheet, react-native-keyboard-controller, phosphor-react-native icons, Fraunces + DM Sans fonts. Warm-editorial theme in `src/theme.ts` (Terracotta CTA, Sage status, Warm Paper bg).
- **Backend:** FastAPI + MongoDB (motor). Session-token auth (email/password bcrypt + Emergent Google OAuth unified into `user_sessions`). Emergent Object Storage for cover/profile photos.
- **Navigation:** 5 custom bottom tabs (Discover, Map, My Books, Swaps, Profile) + floating center "+ Add Book" FAB.

## User personas
1. **Reader with unused books** — lists books, discovers nearby readers, swaps locally.
2. **Discoverer** — browses people-first, requests swaps without knowing exactly which book they want.
3. **Erasmus / student** — wants local, trustworthy, visual community with ratings.

## Core requirements (static)
- Auth (email/password + Google), profiles, "Currently exchanging" toggle.
- Book inventory (cover photo, title, author, condition, language, genre; status Available/Reserved/Swapped).
- Discover people & books with distance / genre / language / exchanging filters.
- Neighborhood-density Map of active exchangers (privacy: neighborhood-level only).
- General swap request → in-swap chat → specific book proposal → accept/decline → complete (both) → rate.
- Ratings recompute profile rating & swaps count.

## Iteration 4 — Multilingual support + Arabic RTL (2026-09-16)
- ✅ **5 fully supported languages**: 🇺🇿 O'zbek (`uz`), 🇬🇧 English (`en`), 🇷🇺 Русский (`ru`), 🇮🇹 Italiano (`it`), 🇸🇦 العربية (`ar`). English is the default/fallback.
- ✅ **i18n architecture**: `i18next` + `react-i18next` with namespaced translation keys (`common`, `languageSelect`, `auth`, `tabs`, `discover`, `map`, `books`, `swaps`, `profile`, `person`, `bookDetail`, `addBook`, `scan`, `swapChat`, `reviews`, `wishlist`, `editProfile`, `badges`, `cards`, `status`, `validation`, `errors`, `confirmations`, `emptyStates`). **335 keys per locale, verified in parity across all 5 files.** No hard-coded user-facing strings remain (all screens + shared components use `t()`).
- ✅ **First-time language selection**: `app/(auth)/language.tsx` shown before signup/login for brand-new users ("Please select your language" + 5 language cards). `RootNavigator` routes new users there.
- ✅ **Persistence (dual)**: locally via AsyncStorage (`bookloop.language`) and server-side via `preferred_language` on the user profile (`ProfileUpdate` + `public_user` in `backend/server.py`). `LanguageSync` in `app/_layout.tsx` reconciles server → device on login.
- ✅ **Change language later**: Profile → Language (`app/settings/language.tsx`) modal.
- ✅ **Arabic RTL**: `I18nManager.allowRTL/forceRTL` driven by `LanguageProvider`; `useRTL()` hook; `DirectionalIcon` wrapper mirrors only direction-dependent icons (back/forward arrows, chevrons) while leaving stars/hearts/cameras unchanged; `AppText`/`Field` right-align + `writingDirection: "rtl"` for Arabic. Direction change requires an app reload (RN reads `isRTL` at startup).
- ✅ **Arabic font**: bundled **Noto Sans Arabic** (Regular/Medium/Bold) in `assets/fonts`, registered in `src/typography.ts` as `ARABIC_FONTS`; `fontsForLanguage(language)` returns it for `ar`, else Fraunces/DM Sans. Applied in `AppText`, `Field`, `BookCover`, `StatusBadge`, and screen-level inline styles (since `makeStyles` factories only receive `colors`).
- ✅ **Mixed text + number/date formatting**: centralized `localeFor()`, `formatDate()`, `formatNumber()` in `src/i18n/index.ts` (locale tags `uz-UZ`, `en-US`, `ru-RU`, `it-IT`, `ar`) so dates/numbers render with the correct locale (Arabic-Indic digits + Arabic month names).
- ✅ **Error boundary localized**: `src/components/error-boundary.tsx` now uses `errors.crashTitle/crashBody/reloadApp/showDetails/hideDetails`.
- ✅ Verified: `npx tsc --noEmit` reports only 5 pre-existing unrelated errors (DensityMap module resolution, `_layout` segments comparison, `ui.tsx` StyleProp/ImageStyle overflow, `theme.ts` ColorScheme typing); locale key parity script passes (335 keys × 5).

## Iteration 2 — fixes & features (2026-06-10)
- ✅ Official BookLoop logo (processed transparent PNG) on auth card + Discover header (`src/components/Logo.tsx`).
- ✅ Invite Friends on Profile: native Share + copy invite link (`?ref={user_id}`).
- ✅ Fast Add Book: Search (OpenLibrary proxy `/api/books/search`) + Barcode scan (expo-camera `/book/scan` → `/api/books/isbn/{isbn}`) + Manual, with autofill of title/author/cover/isbn/language.
- ✅ Robust photo upload: XHR `uploadWithProgress` (progress + real errors), Gallery + Camera sources, permission handling. Wired into Add Book cover, Edit Profile avatar, and chat photos.
- ✅ Generated placeholder covers (colored gradient + title) so a book never shows a broken image; remote-image error fallback in `BookCover`.
- ✅ Chat photos: `POST /swaps/{id}/messages {image_url}` + image bubbles.
- ✅ Reviews on profiles: tappable summary (avg + count) → full `/reviews/{id}` list with reviewer, stars, text, date.
- ✅ Map rebuilt as a real interactive Leaflet/OpenStreetMap map (pan/pinch/zoom) with sage neighbourhood count markers → cluster bottom sheet; web uses an iframe, native uses react-native-webview.
- ✅ Books now carry an `isbn` field. Verified: 43/43 backend tests + frontend flows.

## Notes / known limits
- Barcode scanning & camera capture require a real device (not testable in web preview / Expo Go simulator screenshot).
- OpenLibrary is proxied server-side to avoid the client-side Google Books 429 rate limits seen on shared IPs.

## Implemented (2026-06-09)- ✅ Email/password + Google auth, session persistence, root-layout auth gate.
- ✅ Discover (People/Books segment, search, filter bottom sheet: distance/language/exchanging, genre chip row).
- ✅ Map screen with lat/lng-positioned Sage cluster markers, density headline, cluster bottom sheet + Explore People.
- ✅ My Books grid with Available/Reserved/Swapped filter chips; Add Book modal (manual entry + cover upload to object storage); edit/soft-delete.
- ✅ Person profile with available books, reviews, Request Swap CTA.
- ✅ Swaps: incoming/outgoing/active/completed buckets; swap chat with proposal picker sheet, accept/decline/cancel, mark-complete (dual-confirm), rating sheet; incoming-request badge on tab.
- ✅ Profile: rating/swaps/books stats, Currently-exchanging toggle, genres/languages, my-books preview, edit-profile modal, logout.
- ✅ Derived notifications endpoint.
- ✅ Seeded Messina demo community (8 users, ~22 books, real cover art).
- ✅ Tested: 33/33 backend pytest; frontend flows verified. Fixed `_add_message` ObjectId leak (500 on send message).

## Backlog / future
- **P1:** ISBN/barcode scan + Google Books lookup for faster add-book; pull-to-refresh polish; push notifications (on request; needs build).
- **P1:** Real GPS location permission + native map (react-native-maps) for production build.
- **P2 (roadmap V2/V3):** AI "people you may enjoy swapping with", Smart Swap multi-person chains, university/phone verification badges, premium/promoted listings.

## Test credentials
See `/app/memory/test_credentials.md` (test@bookloop.com / test1234).
