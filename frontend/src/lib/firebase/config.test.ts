import { readFirebaseConfig } from './config';

const FULL = {
  NEXT_PUBLIC_FIREBASE_API_KEY: 'test-api-key',
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: 'example.firebaseapp.com',
  NEXT_PUBLIC_FIREBASE_PROJECT_ID: 'example-project',
  NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: 'example.appspot.com',
  NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: '123',
  NEXT_PUBLIC_FIREBASE_APP_ID: '1:123:web:abc',
  NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID: 'G-TEST',
};

describe('readFirebaseConfig', () => {
  it('returns the documented public config when every variable is present', () => {
    const result = readFirebaseConfig(FULL);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.config.projectId).toBe('example-project');
      expect(result.config.appId).toBe('1:123:web:abc');
    }
  });

  it('reports Firebase configuration absence by variable NAME, never by value', () => {
    const result = readFirebaseConfig({ ...FULL, NEXT_PUBLIC_FIREBASE_API_KEY: undefined, NEXT_PUBLIC_FIREBASE_APP_ID: '   ' });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.missing).toEqual(['NEXT_PUBLIC_FIREBASE_API_KEY', 'NEXT_PUBLIC_FIREBASE_APP_ID']);
      expect(JSON.stringify(result)).not.toContain('test-api-key');
    }
  });

  it('treats a completely empty environment as misconfigured', () => {
    const result = readFirebaseConfig({});
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.missing).toHaveLength(4);
  });

  it('does not require the optional analytics/storage variables', () => {
    const result = readFirebaseConfig({
      NEXT_PUBLIC_FIREBASE_API_KEY: 'k',
      NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: 'd',
      NEXT_PUBLIC_FIREBASE_PROJECT_ID: 'p',
      NEXT_PUBLIC_FIREBASE_APP_ID: 'a',
    });
    expect(result.ok).toBe(true);
  });
});
