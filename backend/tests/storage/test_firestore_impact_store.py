"""FirestoreImpactStore against the real emulator: transactional leasing with a
fencing owner, immutable DONE batches, first-writer-wins finalization, tenant
isolation, and a full connector-impact run whose checkpoints live in Firestore."""

import uuid

import pytest

from app.impact.models import BatchState, ImpactJob, ImpactStatus, LeaseResult
from app.impact.store import FirestoreImpactStore
from tests.impact.conftest import (  # noqa: F401 (fixtures below)  # noqa: F401
    fast_config,
    make_harness,
    package,
    snapshot,
)

pytestmark = pytest.mark.usefixtures("firestore_emulator_db")


@pytest.fixture
def store(firestore_emulator_db) -> FirestoreImpactStore:
    return FirestoreImpactStore("demo-rateguard-tests", collection=f"impact_{uuid.uuid4().hex[:8]}")


def _job(tenant="tenant-a", jid="IJ-1", mission="MIS-1") -> ImpactJob:
    return ImpactJob(tenant_id=tenant, job_id=jid, mission_id=mission, as_of="2026-09-20", product="az_ho3",
                     batch_size=10, batch_count=2, rows_total=20, rows_in_scope=20)


def test_create_is_idempotent_and_tenant_scoped(store):
    a = store.create_job(_job())
    again = store.create_job(_job().model_copy(update={"product": "different"}))
    assert again.product == a.product == "az_ho3"
    assert store.get_job("tenant-b", "IJ-1") is None
    assert [j.job_id for j in store.find_jobs_for_mission("tenant-a", "MIS-1")] == ["IJ-1"]
    assert store.find_jobs_for_mission("tenant-b", "MIS-1") == []


def test_lease_fencing_and_immutable_done(store):
    store.create_job(_job().model_copy(update={"status": ImpactStatus.RUNNING}))
    store.ensure_batches("tenant-a", "IJ-1", [(0, 0, 10), (1, 10, 20)])
    store.ensure_batches("tenant-a", "IJ-1", [(0, 0, 10), (1, 10, 20)])  # idempotent
    assert len(store.list_batches("tenant-a", "IJ-1")) == 2
    res, b = store.lease_batch("tenant-a", "IJ-1", 0, "A", 60, 3)
    assert res == LeaseResult.ACQUIRED and b.attempts == 1
    assert store.lease_batch("tenant-a", "IJ-1", 0, "B", 60, 3)[0] == LeaseResult.LEASED_ELSEWHERE
    assert store.complete_batch("tenant-a", "IJ-1", 0, "B", {"matched": 5}, BatchState.DONE) is False
    assert store.complete_batch("tenant-a", "IJ-1", 0, "A", {"matched": 10, "affected": [[1, "1.00", "2.00"]],
                                                            "inconclusive": []}, BatchState.DONE) is True
    done = store.get_batch("tenant-a", "IJ-1", 0)
    assert done.state == BatchState.DONE and done.matched == 10 and done.affected == [[1, "1.00", "2.00"]]
    assert store.lease_batch("tenant-a", "IJ-1", 0, "C", 60, 3)[0] == LeaseResult.ALREADY_DONE
    assert store.lease_batch("tenant-b", "IJ-1", 0, "C", 60, 3)[0] == LeaseResult.NOT_FOUND
    assert store.get_batch("tenant-b", "IJ-1", 0) is None


def test_finalize_first_writer_wins_and_closes_job(store):
    store.create_job(_job())
    job, created = store.finalize_job("tenant-a", "IJ-1", ImpactStatus.PARTIAL, {"x": 1})
    assert created and job.aggregate == {"x": 1}
    job2, created2 = store.finalize_job("tenant-a", "IJ-1", ImpactStatus.COMPLETE, {"x": 2})
    assert not created2 and job2.aggregate == {"x": 1}
    store.ensure_batches("tenant-a", "IJ-1", [(0, 0, 10)])
    assert store.lease_batch("tenant-a", "IJ-1", 0, "A", 60, 3)[0] == LeaseResult.JOB_CLOSED


def test_full_impact_run_through_firestore_checkpoints(store, snapshot, package):  # noqa: F811
    h = make_harness(snapshot, package, store=store, config=fast_config())
    job, snap = h.plan(snapshot, package, version="defective-v1")
    a = h.run(job, snap, package)
    h.dispatcher.close()
    assert a.status == ImpactStatus.COMPLETE and a.impact_decision == "BLOCK" and a.mismatches > 0
    stored = store.get_job("tenant-a", job.job_id)
    assert stored.finalized_at and stored.aggregate["result_sha256"] == a.result_sha256
    assert store.get_job("tenant-b", job.job_id) is None
