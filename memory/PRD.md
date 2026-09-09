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

## Implemented (2026-06-09)
- ✅ Email/password + Google auth, session persistence, root-layout auth gate.
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
