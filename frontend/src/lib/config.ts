/**
 * Browser-visible configuration, read once (Rules.md §3.1's rule, applied to the frontend).
 *
 * Only NEXT_PUBLIC_ variables reach the browser, and nothing secret may ever be one (Rules.md P2).
 * The API runs on the laptop (Architecture.md §11); 127.0.0.1, not localhost (see there).
 */
export const API_URL: string = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
