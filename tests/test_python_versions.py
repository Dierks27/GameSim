"""Every source file must parse on the oldest supported Python (requires-python >= 3.11).
Catches 3.12-only syntax such as nested same-quote f-strings (PEP 701)."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECK = ("import ast, pathlib, sys\n"
         "bad = []\n"
         "for p in sys.argv[1:]:\n"
         "    try: ast.parse(pathlib.Path(p).read_text(encoding='utf-8'), filename=p)\n"
         "    except SyntaxError as e: bad.append(f'{p}:{e.lineno}: {e.msg}')\n"
         "print('\\n'.join(bad)); sys.exit(1 if bad else 0)\n")


@pytest.mark.parametrize("exe", ["python3.11", "python3.12"])
def test_sources_parse_on_old_python(exe):
    py = shutil.which(exe)
    if py is None:
        pytest.skip(f"{exe} not installed")
    files = [str(p) for d in ("gridsim", "app", "tests") for p in (ROOT / d).rglob("*.py")]
    r = subprocess.run([py, "-c", CHECK, *files], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
