/**
 * @jest-environment jsdom
 */
import '@testing-library/jest-dom';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AuthProvider, useAuth } from './AuthProvider';
import { getAuthToken, resetAuthHooksForTests } from './session';

const mockAuth: { currentUser: { uid: string; email: string; getIdToken: jest.Mock } | null } = { currentUser: null };
let tokenListener: ((user: unknown) => void | Promise<void>) | null = null;
const mockSignIn = jest.fn();
const mockSignOut = jest.fn();
const mockFetchSession = jest.fn();

jest.mock('firebase/auth', () => ({
  onIdTokenChanged: (_auth: unknown, cb: (user: unknown) => void) => {
    tokenListener = cb;
    return () => {
      tokenListener = null;
    };
  },
  signInWithEmailAndPassword: (...args: unknown[]) => mockSignIn(...args),
  signOut: (...args: unknown[]) => mockSignOut(...args),
}));

const mockGetFirebaseAuth = jest.fn();
jest.mock('../firebase/client', () => {
  class FirebaseConfigError extends Error {
    missing: string[];
    constructor(missing: string[]) {
      super('cfg');
      this.missing = missing;
    }
  }
  return { FirebaseConfigError, getFirebaseAuth: () => mockGetFirebaseAuth() };
});

jest.mock('../api/client', () => {
  const actual = jest.requireActual('../api/client');
  return { ...actual, fetchSession: () => mockFetchSession() };
});

function Probe() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="status">{auth.status}</span>
      <span data-testid="role">{auth.session?.role ?? 'none'}</span>
      <span data-testid="problem">{auth.sessionProblem ?? 'none'}</span>
      <span data-testid="missing">{auth.missingConfig.join(',')}</span>
      <button onClick={() => auth.signIn('a@b.test', 'pw').catch((e) => screen.getByTestId('err').replaceChildren(e.message))}>in</button>
      <button onClick={() => void auth.signOut()}>out</button>
      <span data-testid="err" />
    </div>
  );
}

const emit = async (user: unknown) => {
  await act(async () => {
    await tokenListener?.(user);
  });
};

beforeEach(() => {
  tokenListener = null;
  mockSignIn.mockReset();
  mockSignOut.mockReset();
  mockFetchSession.mockReset();
  mockGetFirebaseAuth.mockReset();
  mockAuth.currentUser = null;
  mockGetFirebaseAuth.mockReturnValue(mockAuth);
  resetAuthHooksForTests();
});

it('starts loading, then becomes signed-out when Firebase has no user', async () => {
  render(<AuthProvider><Probe /></AuthProvider>);
  expect(screen.getByTestId('status')).toHaveTextContent('loading');
  await emit(null);
  expect(screen.getByTestId('status')).toHaveTextContent('signed-out');
});

it('becomes signed-in and loads the SERVER role for a signed-in user', async () => {
  mockFetchSession.mockResolvedValue({ uid: 'u1', email: 'a@b.test', tenant_id: 't', role: 'VIEWER' });
  render(<AuthProvider><Probe /></AuthProvider>);
  await emit({ uid: 'u1', email: 'a@b.test' });
  await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'));
  expect(screen.getByTestId('role')).toHaveTextContent('VIEWER');
});

it('does not refetch the session when only the ID token refreshes for the same user', async () => {
  mockFetchSession.mockResolvedValue({ uid: 'u1', email: 'a@b.test', tenant_id: 't', role: 'ADMIN' });
  render(<AuthProvider><Probe /></AuthProvider>);
  await emit({ uid: 'u1', email: 'a@b.test' });
  await emit({ uid: 'u1', email: 'a@b.test' });
  expect(mockFetchSession).toHaveBeenCalledTimes(1);
});

it('flags an unprovisioned account (server 403) instead of guessing a role', async () => {
  const { ApiError } = jest.requireActual('../api/client');
  mockFetchSession.mockRejectedValue(new ApiError('nope', 403, 'ACCOUNT_NOT_PROVISIONED'));
  render(<AuthProvider><Probe /></AuthProvider>);
  await emit({ uid: 'u2', email: 'x@y.test' });
  await waitFor(() => expect(screen.getByTestId('problem')).toHaveTextContent('not-provisioned'));
  expect(screen.getByTestId('role')).toHaveTextContent('none');
});

it('registers a token getter that returns the current user token and honours forceRefresh', async () => {
  const getIdToken = jest.fn(async (force: boolean) => (force ? 'fresh' : 'cached'));
  mockAuth.currentUser = { uid: 'u1', email: 'a@b.test', getIdToken };
  mockFetchSession.mockResolvedValue({ uid: 'u1', email: null, tenant_id: 't', role: 'VIEWER' });
  render(<AuthProvider><Probe /></AuthProvider>);
  await emit(mockAuth.currentUser);
  expect(await getAuthToken(false)).toBe('cached');
  expect(await getAuthToken(true)).toBe('fresh');
});

it('signIn calls Firebase email/password sign-in and maps errors to a generic message', async () => {
  const user = userEvent.setup();
  mockSignIn.mockRejectedValue({ code: 'auth/user-not-found', message: 'There is no user record for a@b.test' });
  render(<AuthProvider><Probe /></AuthProvider>);
  await emit(null);
  await user.click(screen.getByText('in'));
  await waitFor(() => expect(screen.getByTestId('err')).toHaveTextContent('Invalid email or password.'));
  expect(screen.getByTestId('err')).not.toHaveTextContent('a@b.test');
  expect(mockSignIn).toHaveBeenCalledWith(mockAuth, 'a@b.test', 'pw');
});

it('signOut calls Firebase sign-out', async () => {
  const user = userEvent.setup();
  render(<AuthProvider><Probe /></AuthProvider>);
  await emit(null);
  await user.click(screen.getByText('out'));
  expect(mockSignOut).toHaveBeenCalledWith(mockAuth);
});

it('reports a misconfigured build (Firebase configuration absence) by variable name', async () => {
  const { FirebaseConfigError } = jest.requireMock('../firebase/client');
  mockGetFirebaseAuth.mockImplementation(() => {
    throw new FirebaseConfigError(['NEXT_PUBLIC_FIREBASE_API_KEY']);
  });
  render(<AuthProvider><Probe /></AuthProvider>);
  await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('misconfigured'));
  expect(screen.getByTestId('missing')).toHaveTextContent('NEXT_PUBLIC_FIREBASE_API_KEY');
});
