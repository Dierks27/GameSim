"""Print GridSim's dependency list ([project].dependencies in pyproject.toml), one requirement per line.

The Windows build feeds this to pip, so the installed program gets exactly what the source install gets and the
list lives in one place. GridSim itself is NOT installed into the private Python: its source ships as files in
<root>\\program and is found through python313._pth.

    python -I packaging/print_deps.py [path/to/pyproject.toml]
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def dependencies(pyproject: Path) -> list[str]:
    with open(pyproject, "rb") as fh:
        deps = tomllib.load(fh)["project"]["dependencies"]
    if not deps or not all(isinstance(d, str) and d.strip() for d in deps):
        raise SystemExit(f"{pyproject}: [project].dependencies is empty or not a list of requirement strings")
    return [d.strip() for d in deps]


def main(argv: list[str]) -> int:
    pyproject = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent / "pyproject.toml"
    sys.stdout.write("\n".join(dependencies(pyproject)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
