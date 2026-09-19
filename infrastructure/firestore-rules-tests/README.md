# Firestore rules tests (emulator only)

Proves that `../firestore.rules` denies every browser (client-SDK) read, write, list and
query — anonymous or signed in — on every collection, while server-side access is unaffected.
**Nothing in this directory publishes rules or touches a cloud project.**

Requires Java 11+ (Firestore emulator) and Node. From this directory:

```bash
npm install
npx firebase emulators:exec --only firestore --project demo-rateguard-rules --config ../firebase.json "npm test"
```

Publishing the rules is a separate, reviewed step (see `docs/security/AUTHORIZATION_MATRIX.md`, "Firestore rules and indexes").
