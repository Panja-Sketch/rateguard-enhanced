/**
 * Bridge between the React auth provider and the plain-TypeScript API client.
 *
 * The provider registers how to obtain a Firebase ID token and what to do when
 * the session is no longer valid; the API client (which is not a React
 * component) calls these. Tokens are only ever held in memory by the Firebase
 * SDK — never written to localStorage, URLs, logs, or error messages.
 */

export type TokenGetter = (forceRefresh: boolean) => Promise<string | null>;

let tokenGetter: TokenGetter | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function registerAuthHooks(getter: TokenGetter, onUnauthorized: () => void): () => void {
  tokenGetter = getter;
  unauthorizedHandler = onUnauthorized;
  return () => {
    if (tokenGetter === getter) tokenGetter = null;
    if (unauthorizedHandler === onUnauthorized) unauthorizedHandler = null;
  };
}

/** Returns a current ID token (the SDK refreshes an expired one transparently),
 * or null when nobody is signed in. `forceRefresh` bypasses the SDK cache. */
export async function getAuthToken(forceRefresh = false): Promise<string | null> {
  if (!tokenGetter) return null;
  try {
    return await tokenGetter(forceRefresh);
  } catch {
    return null;
  }
}

/** Called by the API client when the server still answers 401 after a forced
 * token refresh: the session is over. */
export function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}

export function resetAuthHooksForTests(): void {
  tokenGetter = null;
  unauthorizedHandler = null;
}
