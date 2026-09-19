import { ApiError, authFetch, getAssuranceMission } from './client';
import { registerAuthHooks, resetAuthHooksForTests } from '../auth/session';

const TOKEN = 'header.payload.SECRETSIGNATURE';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('authFetch', () => {
  const realFetch = global.fetch;
  let fetchMock: jest.Mock;
  let unauthorized: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    unauthorized = jest.fn();
  });

  afterEach(() => {
    global.fetch = realFetch;
    resetAuthHooksForTests();
  });

  it('attaches the Firebase ID token as a Bearer header — never in the URL', async () => {
    registerAuthHooks(async () => TOKEN, unauthorized);
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));

    await authFetch('http://api.test/api/v1/missions?limit=5', { method: 'GET' });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).not.toContain(TOKEN);
    expect(String(url)).not.toMatch(/token|access_token|id_token/i);
    expect(new Headers(init.headers).get('Authorization')).toBe(`Bearer ${TOKEN}`);
  });

  it('preserves caller headers and does not clobber a multipart Content-Type', async () => {
    registerAuthHooks(async () => TOKEN, unauthorized);
    fetchMock.mockResolvedValue(jsonResponse(200, {}));
    await authFetch('http://api.test/x', { method: 'POST', headers: { 'X-Trace': '1' } });
    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get('X-Trace')).toBe('1');
    expect(headers.has('Content-Type')).toBe(false);
  });

  it('refreshes the token once on 401 and retries with the new token', async () => {
    const getter = jest.fn(async (force: boolean) => (force ? 'REFRESHED' : 'STALE'));
    registerAuthHooks(getter, unauthorized);
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {})).mockResolvedValueOnce(jsonResponse(200, { ok: 1 }));

    const res = await authFetch('http://api.test/x');

    expect(res.status).toBe(200);
    expect(getter).toHaveBeenNthCalledWith(1, false);
    expect(getter).toHaveBeenNthCalledWith(2, true);
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get('Authorization')).toBe('Bearer REFRESHED');
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it('ends the session when the server still answers 401 after a refresh', async () => {
    registerAuthHooks(async () => TOKEN, unauthorized);
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: { code: 'INVALID_TOKEN', message: 'x' } }));

    await expect(getAssuranceMission('MIS-1')).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      message: 'Your session has expired. Please sign in again.',
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it('treats 403 as a permission decision: no retry, no sign-out, safe message', async () => {
    registerAuthHooks(async () => TOKEN, unauthorized);
    fetchMock.mockResolvedValue(jsonResponse(403, { detail: { code: 'INSUFFICIENT_ROLE', message: 'server text' } }));

    let caught: unknown;
    try {
      await getAssuranceMission('MIS-1');
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(ApiError);
    const apiError = caught as ApiError;
    expect(apiError.status).toBe(403);
    expect(apiError.code).toBe('INSUFFICIENT_ROLE');
    expect(apiError.message).toBe('You do not have permission to perform this action.');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it('fails as signed-out without touching the network when there is no token', async () => {
    registerAuthHooks(async () => null, unauthorized);
    await expect(authFetch('http://api.test/x')).rejects.toMatchObject({ status: 401, code: 'AUTHENTICATION_REQUIRED' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('never leaks the token into an error message', async () => {
    registerAuthHooks(async () => TOKEN, unauthorized);
    fetchMock.mockResolvedValue(jsonResponse(500, { detail: 'boom' }));
    try {
      await getAssuranceMission('MIS-1');
    } catch (err) {
      expect(String((err as Error).message)).not.toContain(TOKEN);
    }
  });

  it('shows a safe rate-limit message with the Retry-After hint and neither retries nor signs out', async () => {
    registerAuthHooks(async () => TOKEN, unauthorized);
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: 'RATE_LIMITED', message: 'x' } }), {
        status: 429,
        headers: { 'Content-Type': 'application/json', 'Retry-After': '1800' },
      }),
    );
    await expect(getAssuranceMission('MIS-1')).rejects.toMatchObject({
      status: 429,
      code: 'RATE_LIMITED',
      message: 'You have made too many requests. Try again in about 30 minutes.',
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(unauthorized).not.toHaveBeenCalled();
  });
});
