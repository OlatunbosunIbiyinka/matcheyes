import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def imported_modules(path: Path) -> set[str]:
    """Absolute imports in a file. `from pkg import name` yields both `pkg` and `pkg.name`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def python_files(package: str) -> list[Path]:
    return sorted((SRC_ROOT / package).rglob("*.py"))
