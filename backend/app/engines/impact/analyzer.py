from app.engines.diff.models import SemanticDiffResult
from app.engines.impact.graph import PricingDependencyGraph
from app.engines.impact.models import ImpactAnalysis, ImpactPredicate
from app.engines.impact.predicates import derive_predicate_from_difference
from app.ipir.package import IPIRPackage


class ImpactAnalyzer:
    """Analyzes semantic diff results against an IPIR package dependency graph."""

    def analyze(self, diff_result: SemanticDiffResult, package: IPIRPackage) -> ImpactAnalysis:
        """Derives downstream affected nodes, paths, outputs, and risk predicates.

        Args:
            diff_result: SemanticDiffResult instance from comparator.
            package: Base IPIRPackage instance.

        Returns:
            ImpactAnalysis detailing blast-radius and candidate risk predicates.
        """
        graph = PricingDependencyGraph(package)

        changed_nodes = sorted(list({d.node_id for d in diff_result.differences}))
        all_descendants: set[str] = set()
        affected_outputs: set[str] = set()
        affected_coverages: set[str] = set()
        dependency_paths: list[list[str]] = []
        candidate_predicates: list[ImpactPredicate] = []

        output_ids = {o.id for o in package.outputs}
        coverage_ids = {c.id for c in package.coverages}

        for diff in diff_result.differences:
            node_id = diff.node_id
            descendants = graph.get_descendants(node_id)
            all_descendants.update(descendants)

            # Check affected outputs and coverages
            for desc in descendants:
                if desc in output_ids:
                    affected_outputs.add(desc)
                if desc in coverage_ids:
                    affected_coverages.add(desc)

            # Derive paths to outputs
            for out_id in output_ids:
                paths = graph.get_paths(node_id, out_id)
                dependency_paths.extend(paths)

            # Derive risk predicates -- always returns one, see its docstring.
            candidate_predicates.append(derive_predicate_from_difference(diff, package))

        directly_affected: set[str] = set()
        for cnode in changed_nodes:
            if cnode in graph.graph:
                directly_affected.update(list(graph.graph.successors(cnode)))

        return ImpactAnalysis(
            package_id=package.id,
            changed_nodes=changed_nodes,
            directly_affected_nodes=sorted(list(directly_affected)),
            downstream_affected_nodes=sorted(list(all_descendants)),
            affected_outputs=sorted(list(affected_outputs)),
            affected_coverages=sorted(list(affected_coverages)),
            dependency_paths=dependency_paths,
            candidate_risk_predicates=candidate_predicates,
            metadata={
                "total_differences": diff_result.difference_count,
                "diff_result_equal": diff_result.semantically_equal,
            },
        )
