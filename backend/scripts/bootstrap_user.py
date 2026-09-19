"""Controlled admin bootstrap: assign an existing Firebase user a tenant + role.

Writes exactly one document, `users/{firebase_uid}`, in the project's
Firestore. That document is the *only* authority for role and tenant
(locked doc 4.1.A); the frontend and the API request bodies never are.

Safety properties
  * No credentials in this file. Uses Application Default Credentials only
    (`gcloud auth application-default login` locally). No service-account JSON.
  * An explicit `--project-id` is required, and the write only happens when
    `--confirm` repeats that exact project id. Without it the script is a dry
    run that prints the plan and changes nothing.
  * Looks the user up through Firebase Admin by `--email` or `--uid`; it never
    creates Firebase users and never touches passwords.
  * Idempotent: re-running with the same arguments reports "unchanged".
    Moving an existing user to a *different tenant* additionally requires
    `--allow-tenant-change`.
  * Grants no IAM of any kind. Prints only non-sensitive results (project,
    uid, tenant, role, action) -- never tokens, keys, or the email.

Example (deployed acceptance testing -- assign the demo user as ADMIN):

    python backend/scripts/bootstrap_user.py \\
        --project-id rateguard-enhanced \\
        --email <demo-user-email> \\
        --tenant rateguard-demo --role ADMIN \\
        --confirm rateguard-enhanced
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.directory import UserDirectory  # noqa: E402
from app.auth.models import Role, UserRecord  # noqa: E402

TENANT_PATTERN_HELP = "lowercase letters, digits, '-' or '_' (1-64 chars)"


def valid_tenant_id(value: str) -> bool:
    import re

    return re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value) is not None


def plan_assignment(
    existing: UserRecord | None,
    desired: UserRecord,
    *,
    allow_tenant_change: bool,
) -> tuple[str, str | None]:
    """Returns (action, refusal_reason). action is one of
    'create', 'update', 'unchanged', 'refuse'."""
    if existing is None:
        return "create", None
    if existing.tenant_id != desired.tenant_id and not allow_tenant_change:
        return "refuse", "user already belongs to a different tenant; pass --allow-tenant-change to move them"
    if existing.role == desired.role and existing.tenant_id == desired.tenant_id and not existing.disabled:
        return "unchanged", None
    return "update", None


def run_assignment(
    directory: UserDirectory,
    *,
    uid: str,
    email: str | None,
    tenant_id: str,
    role: Role,
    firebase_disabled: bool,
    allow_tenant_change: bool,
    apply: bool,
) -> tuple[str, str | None]:
    if firebase_disabled:
        return "refuse", "the Firebase account is disabled"
    desired = UserRecord(uid=uid, tenant_id=tenant_id, role=role, disabled=False, email=email)
    action, reason = plan_assignment(directory.get_user(uid), desired, allow_tenant_change=allow_tenant_change)
    if action in ("create", "update") and apply:
        directory.put_user(desired)
    return action, reason


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-id", required=True, help="GCP/Firebase project id (explicit; no default).")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--email", help="Email of the existing Firebase user.")
    who.add_argument("--uid", help="Firebase UID of the existing user.")
    parser.add_argument("--tenant", default="rateguard-demo", help=f"Tenant id ({TENANT_PATTERN_HELP}).")
    parser.add_argument("--role", required=True, choices=[r.value for r in Role])
    parser.add_argument("--database", default=None, help="Firestore database id (default: the default database).")
    parser.add_argument("--allow-tenant-change", action="store_true")
    parser.add_argument(
        "--confirm",
        default=None,
        help="Must equal --project-id to actually write. Omit for a dry run.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not valid_tenant_id(args.tenant):
        print(f"ERROR: invalid --tenant ({TENANT_PATTERN_HELP}).", file=sys.stderr)
        return 2
    apply = args.confirm is not None
    if apply and args.confirm != args.project_id:
        print("ERROR: --confirm must repeat --project-id exactly.", file=sys.stderr)
        return 2

    import firebase_admin
    from firebase_admin import auth, credentials

    from app.auth.directory import FirestoreUserDirectory

    firebase_admin.initialize_app(credentials.ApplicationDefault(), options={"projectId": args.project_id})
    try:
        record = auth.get_user_by_email(args.email) if args.email else auth.get_user(args.uid)
    except (auth.UserNotFoundError, ValueError):
        print("ERROR: no matching Firebase user in this project.", file=sys.stderr)
        return 1

    directory = FirestoreUserDirectory(args.project_id, args.database)
    action, reason = run_assignment(
        directory,
        uid=record.uid,
        email=record.email,
        tenant_id=args.tenant,
        role=Role(args.role),
        firebase_disabled=bool(record.disabled),
        allow_tenant_change=args.allow_tenant_change,
        apply=apply,
    )
    mode = "APPLIED" if apply and action in ("create", "update") else ("DRY-RUN" if not apply else "NO-OP")
    print(f"project={args.project_id} uid={record.uid} tenant={args.tenant} role={args.role} action={action} mode={mode}")
    if reason:
        print(f"REFUSED: {reason}", file=sys.stderr)
        return 1
    if not apply:
        print("Dry run only. Re-run with --confirm <project-id> to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
