"""Installed program or source folder: the layout test and the helpers the launcher, scheduler and updater share."""
from __future__ import annotations

from gridsim import installed


def test_a_source_folder_is_not_the_installed_program(tmp_path, monkeypatch):
    assert installed.install_root() is None and not installed.is_installed()
    assert (installed.program_dir() / "gridsim" / "installed.py").exists()
    monkeypatch.setenv("GRIDSIM_STATE", str(tmp_path / "state dir"))
    assert installed.state_dir() == tmp_path / "state dir" and installed.state_dir().is_dir()


def test_the_installed_layout_is_recognised_by_its_private_python(tmp_path):
    root = tmp_path / "Programs" / "GridSim"
    (root / "python").mkdir(parents=True)
    (root / "program" / "gridsim").mkdir(parents=True)
    exe = root / "python" / "pythonw.exe"
    exe.write_text("")
    assert installed.install_root(str(exe)) == root
    other = tmp_path / "venv" / "Scripts" / "python.exe"
    other.parent.mkdir(parents=True)
    other.write_text("")
    assert installed.install_root(str(other)) is None


def test_the_console_and_windowless_pythons_are_found_next_to_each_other(tmp_path, monkeypatch):
    d = tmp_path / "python"
    d.mkdir()
    (d / "python.exe").write_text("")
    (d / "pythonw.exe").write_text("")
    monkeypatch.setattr(installed.sys, "executable", str(d / "pythonw.exe"))
    assert installed.python_exe() == str(d / "python.exe") and installed.pythonw_exe() == str(d / "pythonw.exe")
    (d / "pythonw.exe").unlink()
    monkeypatch.setattr(installed.sys, "executable", str(d / "python.exe"))
    assert installed.pythonw_exe() == str(d / "python.exe")          # no windowless one: the running one
