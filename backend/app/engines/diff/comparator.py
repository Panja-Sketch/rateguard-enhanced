from decimal import Decimal

from app.engines.diff.enums import DifferenceSeverity, DifferenceType
from app.engines.diff.models import SemanticDifference, SemanticDiffResult
from app.engines.diff.table_diff import compare_rate_tables
from app.ipir.common import EffectivePeriod
from app.ipir.package import IPIRPackage


def compare_effective_periods(
    path: str,
    node_id: str,
    node_type: str,
    left_ep: EffectivePeriod | None,
    right_ep: EffectivePeriod | None,
) -> list[SemanticDifference]:
    """Compares two effective periods for effective-date drift."""
    diffs: list[SemanticDifference] = []

    left_start = left_ep.start if left_ep else None
    right_start = right_ep.start if right_ep else None

    if left_start != right_start:
        diffs.append(
            SemanticDifference(
                id=f"diff_{node_id}_effective_start",
                difference_type=DifferenceType.EFFECTIVE_DATE_CHANGE,
                semantic_path=f"{path}.effective_period.start",
                node_id=node_id,
                node_type=node_type,
                left_value=str(left_start) if left_start else None,
                right_value=str(right_start) if right_start else None,
                severity=DifferenceSeverity.HIGH,
                description=(
                    f"Effective start date for {node_type} '{node_id}' changed from "
                    f"{left_start} to {right_start}"
                ),
            )
        )

    return diffs


def compare_packages(left: IPIRPackage, right: IPIRPackage) -> SemanticDiffResult:
    """Deterministically compares two IPIRPackage instances to discover semantic differences."""
    differences: list[SemanticDifference] = []

    # 1. Package Effective Date Drift
    differences.extend(
        compare_effective_periods(
            "package", left.id, "PACKAGE", left.effective_period, right.effective_period
        )
    )

    # 2. Inputs Comparison
    left_inputs = {i.id: i for i in left.inputs}
    right_inputs = {i.id: i for i in right.inputs}
    for inp_id in sorted(set(left_inputs.keys()) | set(right_inputs.keys())):
        left_inp = left_inputs.get(inp_id)
        right_inp = right_inputs.get(inp_id)

        if left_inp and not right_inp:
            differences.append(
                SemanticDifference(
                    id=f"diff_input_{inp_id}_missing",
                    difference_type=DifferenceType.MISSING_NODE,
                    semantic_path=f"inputs.{inp_id}",
                    node_id=inp_id,
                    node_type="INPUT",
                    left_value=left_inp.id,
                    right_value=None,
                    severity=DifferenceSeverity.CRITICAL,
                    description=f"Input '{inp_id}' missing on right side",
                )
            )
        elif not left_inp and right_inp:
            differences.append(
                SemanticDifference(
                    id=f"diff_input_{inp_id}_extra",
                    difference_type=DifferenceType.EXTRA_NODE,
                    semantic_path=f"inputs.{inp_id}",
                    node_id=inp_id,
                    node_type="INPUT",
                    left_value=None,
                    right_value=right_inp.id,
                    severity=DifferenceSeverity.CRITICAL,
                    description=f"Input '{inp_id}' present on right side only",
                )
            )

    # 3. Constants Comparison
    left_consts = {c.id: c for c in left.constants}
    right_consts = {c.id: c for c in right.constants}
    for cid in sorted(set(left_consts.keys()) | set(right_consts.keys())):
        lc = left_consts.get(cid)
        rc = right_consts.get(cid)

        if lc and rc:
            if Decimal(str(lc.value)) != Decimal(str(rc.value)):
                differences.append(
                    SemanticDifference(
                        id=f"diff_constant_{cid}_value",
                        difference_type=DifferenceType.VALUE_CHANGE,
                        semantic_path=f"constants.{cid}.value",
                        node_id=cid,
                        node_type="CONSTANT",
                        left_value=lc.value,
                        right_value=rc.value,
                        severity=DifferenceSeverity.CRITICAL,
                        description=f"Constant '{cid}' value changed from {lc.value} to {rc.value}",
                        left_provenance=lc.provenance,
                        right_provenance=rc.provenance,
                    )
                )

    # 4. Rate Tables Comparison
    left_tables = {t.id: t for t in left.tables}
    right_tables = {t.id: t for t in right.tables}
    for tid in sorted(set(left_tables.keys()) | set(right_tables.keys())):
        lt = left_tables.get(tid)
        rt = right_tables.get(tid)

        if lt and rt:
            differences.extend(compare_rate_tables(lt, rt))

    # 5. Modifiers Comparison
    left_mods = {m.id: m for m in left.modifiers}
    right_mods = {m.id: m for m in right.modifiers}
    for mid in sorted(set(left_mods.keys()) | set(right_mods.keys())):
        lm = left_mods.get(mid)
        rm = right_mods.get(mid)

        if lm and rm:
            # Effective date comparison
            differences.extend(
                compare_effective_periods(
                    f"modifiers.{mid}", mid, "MODIFIER", lm.effective_period, rm.effective_period
                )
            )
            # Value comparison
            if Decimal(str(lm.value)) != Decimal(str(rm.value)):
                differences.append(
                    SemanticDifference(
                        id=f"diff_modifier_{mid}_value",
                        difference_type=DifferenceType.MODIFIER_CHANGE,
                        semantic_path=f"modifiers.{mid}.value",
                        node_id=mid,
                        node_type="MODIFIER",
                        left_value=lm.value,
                        right_value=rm.value,
                        severity=DifferenceSeverity.HIGH,
                        description=f"Modifier '{mid}' value changed from {lm.value} to {rm.value}",
                        left_provenance=lm.provenance,
                        right_provenance=rm.provenance,
                    )
                )
            # Sequence / Order comparison
            if lm.sequence != rm.sequence:
                differences.append(
                    SemanticDifference(
                        id=f"diff_modifier_{mid}_sequence",
                        difference_type=DifferenceType.ORDER_CHANGE,
                        semantic_path=f"modifiers.{mid}.sequence",
                        node_id=mid,
                        node_type="MODIFIER",
                        left_value=lm.sequence,
                        right_value=rm.sequence,
                        severity=DifferenceSeverity.CRITICAL,
                        description=(
                            f"Modifier '{mid}' sequence order changed from {lm.sequence} to "
                            f"{rm.sequence}"
                        ),
                        left_provenance=lm.provenance,
                        right_provenance=rm.provenance,
                    )
                )

    # 6. Constraints Comparison
    left_cons = {c.id: c for c in left.constraints}
    right_cons = {c.id: c for c in right.constraints}
    for cid in sorted(set(left_cons.keys()) | set(right_cons.keys())):
        lc = left_cons.get(cid)
        rc = right_cons.get(cid)

        if lc and rc:
            if Decimal(str(lc.amount)) != Decimal(str(rc.amount)):
                differences.append(
                    SemanticDifference(
                        id=f"diff_constraint_{cid}_amount",
                        difference_type=DifferenceType.CONSTRAINT_CHANGE,
                        semantic_path=f"constraints.{cid}.amount",
                        node_id=cid,
                        node_type="CONSTRAINT",
                        left_value=lc.amount,
                        right_value=rc.amount,
                        severity=DifferenceSeverity.CRITICAL,
                        description=(
                            f"Constraint '{cid}' amount changed from {lc.amount} to {rc.amount}"
                        ),
                        left_provenance=lc.provenance,
                        right_provenance=rc.provenance,
                    )
                )
            if lc.sequence != rc.sequence:
                differences.append(
                    SemanticDifference(
                        id=f"diff_constraint_{cid}_sequence",
                        difference_type=DifferenceType.ORDER_CHANGE,
                        semantic_path=f"constraints.{cid}.sequence",
                        node_id=cid,
                        node_type="CONSTRAINT",
                        left_value=lc.sequence,
                        right_value=rc.sequence,
                        severity=DifferenceSeverity.CRITICAL,
                        description=(
                            f"Constraint '{cid}' sequence order changed from {lc.sequence} to "
                            f"{rc.sequence}"
                        ),
                        left_provenance=lc.provenance,
                        right_provenance=rc.provenance,
                    )
                )

    # 7. Fees Comparison
    left_fees = {f.id: f for f in left.fees}
    right_fees = {f.id: f for f in right.fees}
    for fid in sorted(set(left_fees.keys()) | set(right_fees.keys())):
        lf = left_fees.get(fid)
        rf = right_fees.get(fid)

        if lf and rf:
            if Decimal(str(lf.amount)) != Decimal(str(rf.amount)):
                differences.append(
                    SemanticDifference(
                        id=f"diff_fee_{fid}_amount",
                        difference_type=DifferenceType.FEE_CHANGE,
                        semantic_path=f"fees.{fid}.amount",
                        node_id=fid,
                        node_type="FEE",
                        left_value=lf.amount,
                        right_value=rf.amount,
                        severity=DifferenceSeverity.CRITICAL,
                        description=f"Fee '{fid}' amount changed from {lf.amount} to {rf.amount}",
                        left_provenance=lf.provenance,
                        right_provenance=rf.provenance,
                    )
                )
            if lf.sequence != rf.sequence:
                differences.append(
                    SemanticDifference(
                        id=f"diff_fee_{fid}_sequence",
                        difference_type=DifferenceType.ORDER_CHANGE,
                        semantic_path=f"fees.{fid}.sequence",
                        node_id=fid,
                        node_type="FEE",
                        left_value=lf.sequence,
                        right_value=rf.sequence,
                        severity=DifferenceSeverity.CRITICAL,
                        description=(
                            f"Fee '{fid}' sequence changed from {lf.sequence} to {rf.sequence}"
                        ),
                        left_provenance=lf.provenance,
                        right_provenance=rf.provenance,
                    )
                )

    # Tally severity counts
    severity_counts: dict[str, int] = {}
    for diff in differences:
        sev_str = diff.severity.value
        severity_counts[sev_str] = severity_counts.get(sev_str, 0) + 1

    return SemanticDiffResult(
        left_package_id=left.id,
        right_package_id=right.id,
        left_version=left.version,
        right_version=right.version,
        differences=differences,
        difference_count=len(differences),
        severity_counts=severity_counts,
        semantically_equal=len(differences) == 0,
        comparison_metadata={"diff_engine": "RateGuard Semantic Diff 0.1"},
    )
