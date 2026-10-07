/**
 * Mission name: optional display/evidence metadata. It is never an authorization
 * input and never affects a decision. The rules mirror the API
 * (`normalize_mission_name` in backend/app/api/missions.py), which remains the
 * authority; this only gives the user immediate feedback.
 */

export const DEFAULT_SOURCES_MISSION_NAME = 'Assurance Mission Launched from Sources';
export const MISSION_NAME_MAX_LENGTH = 120;

/** Format shown as guidance for candidate verification (never a real commit SHA). */
export const CANDIDATE_VERIFY_NAME_FORMAT =
  '[CANDIDATE-VERIFY-<first 12 characters of the commit SHA>] controlled workbook vs versioned REST connector';

export type MissionNameResult = { ok: true; value: string } | { ok: false; error: string };

// C0/C1 controls and Unicode format characters (zero-width, bidi overrides).
const FORBIDDEN = /[\u0000-\u001F\u007F-\u009F​-‏‪-‮⁠-⁤⁦-⁯﻿]/;

/**
 * Blank (or all-whitespace) input means "not provided" and yields the default,
 * so existing workflows behave exactly as before. A provided name is trimmed and
 * must be a single visible line of at most 120 characters.
 */
export function normalizeMissionName(raw: string | null | undefined, fallback: string = DEFAULT_SOURCES_MISSION_NAME): MissionNameResult {
  const trimmed = (raw ?? '').trim();
  if (!trimmed) return { ok: true, value: fallback };
  if (trimmed.length > MISSION_NAME_MAX_LENGTH) {
    return { ok: false, error: `Mission name must be at most ${MISSION_NAME_MAX_LENGTH} characters.` };
  }
  if (FORBIDDEN.test(trimmed)) {
    return { ok: false, error: 'Mission name must be a single line without control characters.' };
  }
  return { ok: true, value: trimmed };
}
