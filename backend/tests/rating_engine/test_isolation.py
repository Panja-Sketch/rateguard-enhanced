"""The rating engine is a black box: it must be runnable with no RateGuard code
and its image must not carry any."""

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[2] / "rating_engine"
FORBIDDEN_ROOTS = {"app"}


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_engine_source_never_imports_rateguard_code():
    offenders = {
        str(p.relative_to(ENGINE_DIR)): sorted(_imported_roots(p) & FORBIDDEN_ROOTS)
        for p in ENGINE_DIR.rglob("*.py")
        if "__pycache__" not in p.parts and _imported_roots(p) & FORBIDDEN_ROOTS
    }
    assert offenders == {}


def test_engine_runs_when_rateguard_code_is_unimportable():
    """Start the engine in a fresh interpreter in which `app` is blocked."""
    script = textwrap.dedent(
        """
        import sys
        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name == "app" or name.startswith("app."):
                    raise ImportError("RateGuard code must not be importable by the engine")
        sys.meta_path.insert(0, _Block())
        from fastapi.testclient import TestClient
        from rating_engine.main import app
        with TestClient(app) as c:
            r = c.post("/quote", json={
                "request_id": "r", "engine_version": "canonical-v1", "product_id": "az_ho3",
                "effective_date": "2026-10-01", "transaction_type": "NEW_BUSINESS",
                "inputs": {"roof_age": 25, "dwelling_limit": "300000.00"}})
            assert r.status_code == 200 and r.json()["outputs"]["final_premium"] == "700.00", r.text
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ENGINE_DIR.parent, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "OK" in result.stdout


def test_engine_image_copies_only_the_engine():
    dockerfile = (ENGINE_DIR / "Dockerfile").read_text(encoding="utf-8")
    copies = [line.split()[1] for line in dockerfile.splitlines() if line.strip().upper().startswith("COPY ")]
    assert copies == ["backend/rating_engine/requirements.txt", "backend/rating_engine"]
    assert "pyproject" not in dockerfile


def test_engine_requirements_carry_no_rateguard_stack():
    text = (ENGINE_DIR / "requirements.txt").read_text(encoding="utf-8").lower()
    for heavy in ("google", "firebase", "pandas", "openpyxl", "networkx", "reportlab"):
        assert heavy not in text
