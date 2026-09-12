const nextJest = require('next/jest');

// next/jest wires up SWC compilation (TS/JSX) and tsconfig path aliases
// (@/*) without needing babel-jest/ts-jest as separate dependencies -- it
// ships inside the already-installed `next` package.
const createJestConfig = nextJest({
  dir: './',
});

/** @type {import('jest').Config} */
const customJestConfig = {
  // Focused tests here exercise plain functions only (no DOM rendering, no
  // @testing-library/react is installed) -- 'node' is sufficient and avoids
  // depending on jest-environment-jsdom, which is not installed.
  testEnvironment: 'node',
  testMatch: ['<rootDir>/src/**/*.test.{ts,tsx}'],
  // .next/standalone ships its own copy of package.json, which otherwise
  // collides with the repo root one under Jest's haste module map.
  modulePathIgnorePatterns: ['<rootDir>/.next/'],
};

module.exports = createJestConfig(customJestConfig);
