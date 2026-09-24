import {
  CANDIDATE_VERIFY_NAME_FORMAT,
  DEFAULT_SOURCES_MISSION_NAME,
  MISSION_NAME_MAX_LENGTH,
  normalizeMissionName,
} from './missionName';

const MARKED = '[CANDIDATE-VERIFY-babc5cb90952] controlled workbook vs versioned REST connector';

describe('normalizeMissionName', () => {
  it('keeps the legacy default when the name is blank or omitted', () => {
    for (const blank of ['', '   ', '\t ', undefined, null]) {
      expect(normalizeMissionName(blank)).toEqual({ ok: true, value: DEFAULT_SOURCES_MISSION_NAME });
    }
    expect(DEFAULT_SOURCES_MISSION_NAME).toBe('Assurance Mission Launched from Sources');
  });

  it('accepts the exact candidate-verification marker, trimmed', () => {
    expect(normalizeMissionName(`  ${MARKED}  `)).toEqual({ ok: true, value: MARKED });
    expect(MARKED.length).toBeLessThanOrEqual(MISSION_NAME_MAX_LENGTH);
  });

  it('enforces the maximum length', () => {
    expect(normalizeMissionName('x'.repeat(MISSION_NAME_MAX_LENGTH)).ok).toBe(true);
    expect(normalizeMissionName('x'.repeat(MISSION_NAME_MAX_LENGTH + 1)).ok).toBe(false);
  });

  it('rejects control and format characters', () => {
    for (const bad of ['a\nb', 'a\tb', 'a\u0000b', 'a\u001bb', 'a‮b', 'a​b']) {
      expect(normalizeMissionName(bad).ok).toBe(false);
    }
  });

  it('passes markup through unchanged (rendering escapes it)', () => {
    expect(normalizeMissionName('<img src=x onerror=alert(1)>')).toEqual({ ok: true, value: '<img src=x onerror=alert(1)>' });
  });

  it('documents the verification format without hardcoding a real SHA', () => {
    expect(CANDIDATE_VERIFY_NAME_FORMAT).toContain('[CANDIDATE-VERIFY-<first 12 characters of the commit SHA>]');
    expect(CANDIDATE_VERIFY_NAME_FORMAT).not.toMatch(/[0-9a-f]{12}/);
  });
});
