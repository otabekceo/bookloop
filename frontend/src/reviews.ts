/**
 * Pure review helpers (no React/Expo imports, so plain Node can unit-test them). The server already
 * strips everything about the reviewer from anonymous reviews — these only decide what to render.
 */

export type PublicReview = {
  id: string;
  stars: number;
  review?: string;
  created_at?: string | null;
  /** Missing on older servers: those reviews were always shown with the reviewer's name. */
  is_anonymous?: boolean;
  rater_id?: string | null;
  rater_name?: string | null;
  rater_avatar?: string | null;
};

export type ReviewerView =
  | { anonymous: true; name: null; avatar: null; profileId: null }
  | { anonymous: false; name: string; avatar: string | null; profileId: string | null };

/** Who to show on a review card. Anonymous -> no name/avatar/link at all (the UI says "BookLoop member"). */
export function reviewerView(r: PublicReview): ReviewerView {
  if (r.is_anonymous) return { anonymous: true, name: null, avatar: null, profileId: null };
  return { anonymous: false, name: r.rater_name || "", avatar: r.rater_avatar ?? null, profileId: r.rater_id ?? null };
}

/** The body of POST /swaps/{id}/rate. `is_anonymous` is always sent explicitly; it defaults to false. */
export function rateBody(stars: number, review: string, anonymous = false) {
  return { stars: Math.max(1, Math.min(5, Math.round(stars))), review: review.trim(), is_anonymous: anonymous === true };
}

/** Same limit the server enforces (REVIEW_MAX_CHARS in server.py). */
export const REVIEW_MAX_CHARS = 1000;
