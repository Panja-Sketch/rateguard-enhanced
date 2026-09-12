#!/usr/bin/env python3
"""Non-destructive legacy run audit utility for RateGuard AI."""

import argparse
from datetime import datetime
from app.storage import get_run_store


def audit_legacy_runs() -> None:
    print("[*] Auditing legacy run store records...")
    store = get_run_store()
    records = store.list_runs(limit=500)

    v2_count = 0
    legacy_count = 0
    stale_count = 0

    print("\n--------------------------------------------------------------------------------")
    print(f"{'RECORD ID':<20} | {'TYPE':<22} | {'STATUS':<15} | {'RECOMMENDED ACTION'}")
    print("--------------------------------------------------------------------------------")

    for r in records:
        meta = r.metadata if isinstance(r.metadata, dict) else {}
        is_v2 = r.run_id.startswith("MIS-") or meta.get("record_type") == "ASSURANCE_MISSION_V2"
        status_str = r.status.value if hasattr(r.status, "value") else str(r.status)

        if is_v2:
            v2_count += 1
            rec_action = "RETAIN (V2 Audit Record)" if status_str == "COMPLETED" else "INSPECT / ARCHIVE"
            print(f"{r.run_id:<20} | {'ASSURANCE_MISSION_V2':<22} | {status_str:<15} | {rec_action}")
        else:
            legacy_count += 1
            if status_str in ("INITIATING", "IN_PROGRESS", "CREATED", "PROCESSING"):
                stale_count += 1
                rec_action = "LEGACY STALE — Exclude from V2 history view"
            else:
                rec_action = "LEGACY ARCHIVE — Preserve for historical reference"
            print(f"{r.run_id:<20} | {'LEGACY_RUN_V1':<22} | {status_str:<15} | {rec_action}")

    print("--------------------------------------------------------------------------------")
    print(f"\n[Summary] Total Records: {len(records)} | V2 Missions: {v2_count} | Legacy V1 Runs: {legacy_count} (Stale: {stale_count})")
    print("[Note] Legacy records are retained non-destructively and excluded from default V2 history UI.\n")


if __name__ == "__main__":
    audit_legacy_runs()

