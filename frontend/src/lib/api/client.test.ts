import { ApiError, describeFetchError, isNetworkUnreachableError } from './client';

describe('isNetworkUnreachableError', () => {
  it('recognizes the browser fetch() TypeError thrown on a transport-level failure', () => {
    // This is exactly what fetch() rejects with on a CORS-blocked response,
    // a DNS failure, or a refused connection -- no HTTP response was ever
    // received, so there is no status/body to parse into an ApiError.
    expect(isNetworkUnreachableError(new TypeError('Failed to fetch'))).toBe(true);
  });

  it('does not classify a structured ApiError as a network failure', () => {
    expect(isNetworkUnreachableError(new ApiError('Not found', 404))).toBe(false);
  });

  it('does not classify a plain Error as a network failure', () => {
    expect(isNetworkUnreachableError(new Error('something else went wrong'))).toBe(false);
  });
});

describe('describeFetchError', () => {
  it('replaces a raw transport failure with an actionable message', () => {
    const message = describeFetchError(new TypeError('Failed to fetch'), 'No mission was created.');
    expect(message).toBe('RateGuard API is currently unreachable. No mission was created.');
    expect(message).not.toMatch(/Failed to fetch/);
  });

  it('preserves a structured ApiError message unchanged', () => {
    const apiError = new ApiError('Mission validation failed.', 422);
    expect(describeFetchError(apiError, 'No mission was created.')).toBe('Mission validation failed.');
  });

  it('falls back to String(err) for a non-Error thrown value', () => {
    expect(describeFetchError('a raw string throw', 'No mission was created.')).toBe('a raw string throw');
  });
});

describe('ApiError', () => {
  it('carries status, code, and structured validation issues', () => {
    const issues = [{ field: 'source_a', code: 'REQUIRED', message: 'Source A is required.' }];
    const err = new ApiError('Mission validation failed.', 422, 'VALIDATION_FAILED', issues);
    expect(err.status).toBe(422);
    expect(err.code).toBe('VALIDATION_FAILED');
    expect(err.issues).toEqual(issues);
    expect(err.name).toBe('ApiError');
  });
});
