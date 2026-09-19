/**
 * @jest-environment jsdom
 */
import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AuthContextValue } from '@/lib/auth/AuthProvider';
import { SignInError } from '@/lib/auth/errors';
import { AuthGate } from './AuthGate';
import { Navigation } from '../assurance/Navigation';
import LoginPage from '@/app/login/page';

let authValue: AuthContextValue;
const mockReplace = jest.fn();
let mockPathname = '/missions';
let mockSearch = '';

jest.mock('@/lib/auth/AuthProvider', () => ({
  useAuth: () => authValue,
}));
jest.mock('next/navigation', () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => new URLSearchParams(mockSearch),
}));
jest.mock('next/link', () => ({
  __esModule: true,
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
jest.mock('@/lib/api/client', () => ({ fetchHealth: jest.fn().mockResolvedValue({ status: 'healthy' }) }));

const base: AuthContextValue = {
  status: 'signed-in',
  email: 'user@example.test',
  session: { uid: 'u', email: 'user@example.test', tenant_id: 't', role: 'VIEWER' },
  sessionProblem: null,
  missingConfig: [],
  signIn: jest.fn(),
  signOut: jest.fn(),
};

beforeEach(() => {
  authValue = { ...base, signIn: jest.fn(), signOut: jest.fn() };
  mockReplace.mockReset();
  mockPathname = '/missions';
  mockSearch = '';
});

describe('AuthGate (protected routes)', () => {
  it('shows a loading state while the session is being resolved, and no protected content', () => {
    authValue.status = 'loading';
    render(<AuthGate><p>secret mission data</p></AuthGate>);
    expect(screen.getByTestId('auth-loading')).toBeInTheDocument();
    expect(screen.queryByText('secret mission data')).not.toBeInTheDocument();
  });

  it('redirects a signed-out visitor to /login, preserving the requested path, and renders nothing', async () => {
    authValue.status = 'signed-out';
    mockPathname = '/missions/MIS-1';
    render(<AuthGate><p>secret mission data</p></AuthGate>);
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/login?next=%2Fmissions%2FMIS-1'));
    expect(screen.queryByText('secret mission data')).not.toBeInTheDocument();
  });

  it('renders the page for a signed-in user', () => {
    render(<AuthGate><p>secret mission data</p></AuthGate>);
    expect(screen.getByText('secret mission data')).toBeInTheDocument();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('leaves /login reachable while signed out', () => {
    authValue.status = 'signed-out';
    mockPathname = '/login';
    render(<AuthGate><p>login form</p></AuthGate>);
    expect(screen.getByText('login form')).toBeInTheDocument();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('explains an unprovisioned account and offers sign-out', async () => {
    authValue.sessionProblem = 'not-provisioned';
    authValue.session = null;
    render(<AuthGate><p>secret mission data</p></AuthGate>);
    expect(screen.getByTestId('auth-not-provisioned')).toBeInTheDocument();
    expect(screen.queryByText('secret mission data')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(authValue.signOut).toHaveBeenCalled();
  });

  it('shows a configuration error (names only) when Firebase config is absent', () => {
    authValue.status = 'misconfigured';
    authValue.missingConfig = ['NEXT_PUBLIC_FIREBASE_API_KEY'];
    render(<AuthGate><p>secret mission data</p></AuthGate>);
    expect(screen.getByTestId('auth-misconfigured')).toHaveTextContent('NEXT_PUBLIC_FIREBASE_API_KEY');
    expect(screen.queryByText('secret mission data')).not.toBeInTheDocument();
  });
});

describe('Login page', () => {
  beforeEach(() => {
    authValue.status = 'signed-out';
    authValue.session = null;
    authValue.email = null;
  });

  it('submits email and password to signIn', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(screen.getByLabelText('Email'), 'demo@example.test');
    await user.type(screen.getByLabelText('Password'), 'not-a-real-password');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(authValue.signIn).toHaveBeenCalledWith('demo@example.test', 'not-a-real-password');
  });

  it('shows a generic error and clears the password after a failed attempt', async () => {
    const user = userEvent.setup();
    (authValue.signIn as jest.Mock).mockRejectedValue(new SignInError('Invalid email or password.'));
    render(<LoginPage />);
    await user.type(screen.getByLabelText('Email'), 'demo@example.test');
    await user.type(screen.getByLabelText('Password'), 'wrong');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByTestId('login-error')).toHaveTextContent('Invalid email or password.');
    expect(screen.getByLabelText('Password')).toHaveValue('');
  });

  it('sends a signed-in user to the (validated) next path', async () => {
    authValue.status = 'signed-in';
    mockSearch = 'next=%2Fmissions%2FMIS-9';
    render(<LoginPage />);
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/missions/MIS-9'));
  });

  it('never follows an off-site next parameter', async () => {
    authValue.status = 'signed-in';
    mockSearch = 'next=https%3A%2F%2Fevil.example';
    render(<LoginPage />);
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/'));
  });

  it('reports a missing Firebase configuration instead of a broken form', () => {
    authValue.status = 'misconfigured';
    authValue.missingConfig = ['NEXT_PUBLIC_FIREBASE_APP_ID'];
    render(<LoginPage />);
    expect(screen.getByTestId('login-misconfigured')).toHaveTextContent('NEXT_PUBLIC_FIREBASE_APP_ID');
    expect(screen.queryByTestId('login-form')).not.toBeInTheDocument();
  });

  it('contains no hard-coded credentials', () => {
    const { container } = render(<LoginPage />);
    expect(container.innerHTML).not.toMatch(/@gmail\.com|password=|value="[^"]+"/);
  });
});

describe('Navigation (role-aware, logout)', () => {
  it('hides authoring links from a VIEWER but keeps read links', () => {
    render(<Navigation />);
    expect(screen.queryByText('Start Mission')).not.toBeInTheDocument();
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
    expect(screen.getByText('Mission History')).toBeInTheDocument();
    expect(screen.getByTestId('session-badge')).toHaveTextContent('Viewer');
  });

  it('shows authoring links to a RELEASE_OWNER', () => {
    authValue.session = { uid: 'u', email: 'o@example.test', tenant_id: 't', role: 'RELEASE_OWNER' };
    render(<Navigation />);
    expect(screen.getByText('Start Mission')).toBeInTheDocument();
    expect(screen.getByText('Sources')).toBeInTheDocument();
  });

  it('logs out through the provider', async () => {
    render(<Navigation />);
    await userEvent.click(screen.getByRole('button', { name: /Sign out/ }));
    expect(authValue.signOut).toHaveBeenCalled();
  });

  it('renders nothing when signed out or on the login page', () => {
    authValue.status = 'signed-out';
    const { container, rerender } = render(<Navigation />);
    expect(container).toBeEmptyDOMElement();
    authValue.status = 'signed-in';
    mockPathname = '/login';
    rerender(<Navigation />);
    expect(container).toBeEmptyDOMElement();
  });
});
