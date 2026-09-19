const nextJest = require('next/jest');

// next/jest wires up SWC compilation (TS/JSX) and tsconfig path aliases
// (@/*) without needing babel-jest/ts-jest as separate dependencies -- it
// ships inside the already-installed `next` package.
const createJestConfig = nextJest({
  dir: './',
});

/** @type {import('jest').Config} */
const customJestConfig = {
  // Plain-function tests run in 'node'; component tests opt into jsdom per file
  // with a `@jest-environment jsdom` docblock (jest-environment-jsdom is a
  // devDependency).
  testEnvironment: 'node',
  testMatch: ['<rootDir>/src/**/*.test.{ts,tsx}'],
  // Mirror tsconfig's `@/*` path alias for tests (component tests use `@/...`).
  moduleNameMapper: { '^@/(.*)$': '<rootDir>/src/$1' },
  // .next/standalone ships its own copy of package.json, which otherwise
  // collides with the repo root one under Jest's haste module map.
  modulePathIgnorePatterns: ['<rootDir>/.next/'],
};

module.exports = createJestConfig(customJestConfig);
