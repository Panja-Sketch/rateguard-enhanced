/**
 * User-facing text for sign-in failures.
 *
 * Deliberately coarse: an unknown email and a wrong password produce the SAME
 * message (no user enumeration), and no Firebase error string, token, or
 * request detail is ever surfaced.
 */
const CREDENTIAL_CODES = new Set([
  'auth/invalid-credential',
  'auth/invalid-login-credentials',
  'auth/wrong-password',
  'auth/user-not-found',
  'auth/invalid-email',
  'auth/missing-password',
  'auth/user-disabled',
]);

export function describeSignInError(code: string | undefined): string {
  if (code && CREDENTIAL_CODES.has(code)) {
    return 'Invalid email or password.';
  }
  if (code === 'auth/too-many-requests') {
    return 'Too many attempts. Please wait a few minutes and try again.';
  }
  if (code === 'auth/network-request-failed') {
    return 'Could not reach the sign-in service. Check your connection and try again.';
  }
  return 'Sign-in failed. Please try again.';
}

export class SignInError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SignInError';
  }
}
