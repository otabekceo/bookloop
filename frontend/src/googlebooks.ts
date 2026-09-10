// Book metadata lookup — proxied through our backend (OpenLibrary),
// which avoids client-side CORS/rate-limit issues.
import { apiFetch } from "@/src/api";

export type BookResult = {
  title: string;
  author: string;
  cover_url?: string;
  isbn?: string;
  language: string;
};

export async function searchBooks(query: string): Promise<BookResult[]> {
  if (!query.trim()) return [];
  const data = await apiFetch<{ results: BookResult[] }>(`/api/books/search?q=${encodeURIComponent(query)}`);
  return data.results || [];
}

export async function lookupIsbn(isbn: string): Promise<BookResult | null> {
  const data = await apiFetch<{ result: BookResult }>(`/api/books/isbn/${encodeURIComponent(isbn)}`);
  return data.result || null;
}
