import type { SessionInfo } from '../api/client';

export type Role = SessionInfo['role'];

/**
 * Navigation hints ONLY. The backend re-authorizes every request from the
 * server-controlled user record; hiding a link is a usability nicety, never
 * access control (locked doc 4.1.A). Keep in step with
 * docs/security/AUTHORIZATION_MATRIX.md.
 */
export function canAuthorReleases(role: Role | undefined): boolean {
  return role === 'ADMIN' || role === 'RELEASE_OWNER';
}

export function roleLabel(role: Role | undefined): string {
  switch (role) {
    case 'ADMIN':
      return 'Admin';
    case 'RELEASE_OWNER':
      return 'Release owner';
    case 'CONSUMER_REVIEWER':
      return 'Consumer reviewer';
    case 'VIEWER':
      return 'Viewer';
    default:
      return 'Unknown role';
  }
}
