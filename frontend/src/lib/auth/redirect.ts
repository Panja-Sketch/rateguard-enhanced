/** Only same-origin absolute paths are honoured as a post-login destination, so
 * `?next=` can never become an open redirect. Everything else falls back to `/`. */
export function safeNextPath(next: string | null | undefined): string {
  if (!next) return '/';
  if (!next.startsWith('/') || next.startsWith('//') || next.includes('\\') || next.includes('://')) {
    return '/';
  }
  if (next === '/login' || next.startsWith('/login?')) return '/';
  return next;
}
