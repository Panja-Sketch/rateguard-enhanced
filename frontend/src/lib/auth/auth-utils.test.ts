import { describeSignInError } from './errors';
import { safeNextPath } from './redirect';
import { canAuthorReleases, roleLabel } from './roles';

describe('safeNextPath (no open redirects)', () => {
  it.each([
    ['/missions', '/missions'],
    ['/missions/MIS-1?tab=x', '/missions/MIS-1?tab=x'],
    [null, '/'],
    [undefined, '/'],
    ['', '/'],
    ['//evil.example', '/'],
    ['https://evil.example', '/'],
    ['/\\evil.example', '/'],
    ['javascript:alert(1)', '/'],
    ['missions', '/'],
    ['/login', '/'],
    ['/login?next=/x', '/'],
  ])('%s -> %s', (input, expected) => {
    expect(safeNextPath(input as string | null | undefined)).toBe(expected);
  });
});

describe('describeSignInError (no user enumeration)', () => {
  it('gives the same message for unknown user and wrong password', () => {
    const unknown = describeSignInError('auth/user-not-found');
    expect(describeSignInError('auth/wrong-password')).toBe(unknown);
    expect(describeSignInError('auth/invalid-credential')).toBe(unknown);
    expect(unknown).toBe('Invalid email or password.');
  });

  it('maps throttling and network failures, and hides everything else', () => {
    expect(describeSignInError('auth/too-many-requests')).toMatch(/Too many attempts/);
    expect(describeSignInError('auth/network-request-failed')).toMatch(/Could not reach/);
    expect(describeSignInError('auth/internal-error')).toBe('Sign-in failed. Please try again.');
    expect(describeSignInError(undefined)).toBe('Sign-in failed. Please try again.');
  });
});

describe('role-aware navigation hints', () => {
  it('lets only release authors see the authoring links', () => {
    expect(canAuthorReleases('ADMIN')).toBe(true);
    expect(canAuthorReleases('RELEASE_OWNER')).toBe(true);
    expect(canAuthorReleases('CONSUMER_REVIEWER')).toBe(false);
    expect(canAuthorReleases('VIEWER')).toBe(false);
    expect(canAuthorReleases(undefined)).toBe(false);
  });

  it('labels roles for display', () => {
    expect(roleLabel('CONSUMER_REVIEWER')).toBe('Consumer reviewer');
    expect(roleLabel(undefined)).toBe('Unknown role');
  });
});
