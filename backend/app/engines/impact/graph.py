import networkx as nx

from app.engines.impact.errors import ImpactAnalysisError
from app.ipir.package import IPIRPackage


class PricingDependencyGraph:
    """Directed graph representing semantic dependencies across an IPIR package."""

    def __init__(self, package: IPIRPackage) -> None:
        self.package_id = package.id
        self.graph = nx.DiGraph()
        self._build_graph(package)

    def _build_graph(self, package: IPIRPackage) -> None:
        """Constructs directed edges based on IPIR semantic node relationships."""
        # 1. Inputs
        for inp in package.inputs:
            self.graph.add_node(inp.id, node_type="INPUT")

        # 2. Constants
        for const in package.constants:
            self.graph.add_node(const.id, node_type="CONSTANT")

        # 3. Rate Tables (Inputs -> Table)
        for table in package.tables:
            self.graph.add_node(table.id, node_type="TABLE")
            for dim in table.dimensions:
                self.graph.add_edge(dim.input_ref, table.id)

        # 4. Rules
        for rule in package.rules:
            self.graph.add_node(rule.id, node_type="RULE")

        # 5. Calculation Nodes (Dependencies -> Calculation)
        for calc in package.calculations:
            self.graph.add_node(calc.id, node_type="CALCULATION")
            for dep in calc.depends_on:
                self.graph.add_edge(dep, calc.id)

        # 6. Modifiers (applies_to -> Modifier)
        for mod in package.modifiers:
            self.graph.add_node(mod.id, node_type="MODIFIER")
            self.graph.add_edge(mod.applies_to, mod.id)

        # 7. Constraints (applies_to -> Constraint)
        for con in package.constraints:
            self.graph.add_node(con.id, node_type="CONSTRAINT")
            self.graph.add_edge(con.applies_to, con.id)

        # 8. Fees (applies_to -> Fee)
        for fee in package.fees:
            self.graph.add_node(fee.id, node_type="FEE")
            self.graph.add_edge(fee.applies_to, fee.id)

        # 9. Coverages (Calculations -> Coverage -> Output)
        for cov in package.coverages:
            self.graph.add_node(cov.id, node_type="COVERAGE")
            for cref in cov.calculation_refs:
                self.graph.add_edge(cref, cov.id)
            if cov.output_ref:
                self.graph.add_edge(cov.id, cov.output_ref)

        # 10. Outputs (source_ref -> Output)
        for out in package.outputs:
            self.graph.add_node(out.id, node_type="OUTPUT")
            self.graph.add_edge(out.source_ref, out.id)

        # Verify cycle-free property
        if not nx.is_directed_acyclic_graph(self.graph):
            cycles = list(nx.simple_cycles(self.graph))
            raise ImpactAnalysisError(f"Dependency graph contains cycles: {cycles}")

    def get_descendants(self, node_id: str) -> list[str]:
        """Returns all downstream nodes transitively affected by node_id."""
        if node_id not in self.graph:
            return []
        return sorted(nx.descendants(self.graph, node_id))

    def get_ancestors(self, node_id: str) -> list[str]:
        """Returns all upstream nodes influencing node_id."""
        if node_id not in self.graph:
            return []
        return sorted(nx.ancestors(self.graph, node_id))

    def get_paths(self, source_id: str, target_id: str) -> list[list[str]]:
        """Returns all simple directed paths between source_id and target_id."""
        if source_id not in self.graph or target_id not in self.graph:
            return []
        return list(nx.all_simple_paths(self.graph, source_id, target_id))
