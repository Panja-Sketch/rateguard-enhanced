/**
 * Firebase Emulator tests for infrastructure/firestore.rules.
 *
 * The browser talks only to the RateGuard API, never to Firestore directly, so
 * the rules must deny EVERY client (anonymous or signed in, whatever custom
 * claims it carries) on EVERY collection, while the server side (Admin SDK /
 * server client libraries, which bypass rules) keeps working. These tests run
 * against the local emulator only; nothing here publishes the rules.
 *
 * Run:  npm test   (from an `emulators:exec` shell — see ./README.md)
 */
const fs = require('fs');
const path = require('path');
const {
  initializeTestEnvironment,
  assertFails,
  assertSucceeds,
} = require('@firebase/rules-unit-testing');
const {
  doc, getDoc, setDoc, updateDoc, deleteDoc, collection, getDocs, query, where, collectionGroup, addDoc,
} = require('firebase/firestore');

const RULES = fs.readFileSync(process.env.RULES_FILE || path.join(__dirname, '..', 'firestore.rules'), 'utf8');
const [HOST, PORT] = (process.env.FIRESTORE_EMULATOR_HOST || '127.0.0.1:8085').split(':');

// Every top-level collection / path the application (or a future one) uses.
const DOC_PATHS = [
  'users/uid-admin',
  'users/uid-other',
  'tenants/rateguard-demo',
  'assurance_runs/MIS-1',
  'assurance_runs_staging/MIS-1',
  'assurance_runs/MIS-1/events/EVT-1',
  'assurance_runs/MIS-1/evidence/EV-1',
  'assurance_runs/MIS-1/explanations/EXP-1',
  'rate_limits/abc123',
  'idempotency/rateguard-demo:key1',
  'sources/SRC-1',
  'connectors/rating-engine-demo',
  'some_future_collection/x',
];

let env;

beforeAll(async () => {
  env = await initializeTestEnvironment({
    projectId: 'demo-rateguard-rules',
    firestore: { rules: RULES, host: HOST, port: Number(PORT) },
  });
});

afterAll(async () => {
  await env.cleanup();
});

beforeEach(async () => {
  await env.clearFirestore();
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    for (const p of DOC_PATHS) {
      await setDoc(doc(db, p), { tenant_id: 'rateguard-demo', role: 'VIEWER', count: 1, seeded: true });
    }
  });
});

const clients = {
  anonymous: () => env.unauthenticatedContext(),
  'signed-in viewer': () => env.authenticatedContext('uid-viewer', { role: 'VIEWER', tenant_id: 'rateguard-demo' }),
  'signed-in admin with admin claims': () => env.authenticatedContext('uid-admin', { role: 'ADMIN', admin: true, tenant_id: 'rateguard-demo' }),
  'signed-in other tenant': () => env.authenticatedContext('uid-b', { role: 'ADMIN', tenant_id: 'other-tenant' }),
};

describe.each(Object.keys(clients))('%s browser client', (who) => {
  const db = () => clients[who]().firestore();

  test.each(DOC_PATHS)('cannot read %s', async (p) => {
    await assertFails(getDoc(doc(db(), p)));
  });

  test.each(DOC_PATHS)('cannot write, update or delete %s', async (p) => {
    await assertFails(setDoc(doc(db(), p), { role: 'ADMIN', tenant_id: 'rateguard-demo' }));
    await assertFails(updateDoc(doc(db(), p), { role: 'ADMIN' }));
    await assertFails(deleteDoc(doc(db(), p)));
  });

  test('cannot list or query any collection, including tenant-filtered and collection-group queries', async () => {
    await assertFails(getDocs(collection(db(), 'assurance_runs')));
    await assertFails(getDocs(query(collection(db(), 'assurance_runs'), where('tenant_id', '==', 'rateguard-demo'))));
    await assertFails(getDocs(collection(db(), 'users')));
    await assertFails(getDocs(collection(db(), 'rate_limits')));
    await assertFails(getDocs(collectionGroup(db(), 'events')));
    await assertFails(getDocs(collectionGroup(db(), 'explanations')));
  });

  test('cannot create new documents (including a self-provisioned users/{uid} role record)', async () => {
    await assertFails(addDoc(collection(db(), 'assurance_runs'), { tenant_id: 'rateguard-demo' }));
    await assertFails(setDoc(doc(db(), 'users/brand-new-uid'), { role: 'ADMIN', tenant_id: 'rateguard-demo' }));
  });
});

describe('server side is unaffected (rules-bypassing context == Admin SDK behaviour)', () => {
  test('the seeded users document is intact and readable server-side, and no client write changed it', async () => {
    await assertFails(setDoc(doc(clients['signed-in admin with admin claims']().firestore(), 'users/uid-admin'), { role: 'ADMIN' }));
    await env.withSecurityRulesDisabled(async (ctx) => {
      const snap = await assertSucceeds(getDoc(doc(ctx.firestore(), 'users/uid-admin')));
      expect(snap.data().role).toBe('VIEWER');
    });
  });
});

test('rules file is deny-all with no allow-if-true and no auth-dependent grant', () => {
  const grants = RULES.split('\n').filter((l) => /allow\s/.test(l) && !l.trim().startsWith('//'));
  expect(grants).toHaveLength(1);
  expect(grants[0]).toMatch(/allow read, write: if false;/);
  expect(RULES).not.toMatch(/request\.auth/);
});
