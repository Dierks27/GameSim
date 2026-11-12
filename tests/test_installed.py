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


def test_commands_about_the_program_itself_leave_the_data_folder_alone(tmp_path, monkeypatch):
    """The installer runs "gridsim schedule --repoint" and the uninstaller "schedule --remove": neither may create
    or touch the user's data folder."""
    from gridsim import cli
    data = tmp_path / "GridSim Data"
    monkeypatch.setenv("GRIDSIM_DATA", str(data))
    monkeypatch.setenv("GRIDSIM_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_startup_checks", lambda quiet=False: (_ for _ in ()).throw(AssertionError("data check ran")))
    monkeypatch.setattr(cli, "_log_when_windowless", lambda: (_ for _ in ()).throw(AssertionError("data log opened")))
    from gridsim import update
    monkeypatch.setattr(update, "_ask_github", lambda old: {**old, "error": "offline"})      # (no network in tests)
    assert cli.main(["update", "--check"]) == 0
    cli.main(["schedule", "--status"])
    assert not data.exists()


def test_a_program_command_started_without_a_console_writes_to_the_state_folder(tmp_path, monkeypatch):
    """pythonw (the installer's "schedule --repoint", the uninstaller's "schedule --remove") has no output: what the
    command says goes to program.log in the program's own folder, under a line that names the command and the time."""
    import sys

    from gridsim import cli
    data, state = tmp_path / "GridSim Data", tmp_path / "state"
    monkeypatch.setenv("GRIDSIM_DATA", str(data))
    monkeypatch.setenv("GRIDSIM_STATE", str(state))
    monkeypatch.setattr(sys, "argv", ["gridsim", "schedule", "--status"])
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    code = cli.main(["schedule", "--status"])
    opened = sys.stdout
    monkeypatch.undo()                                       # the real output back before anything is asserted
    opened.close()
    assert code == 0
    lines = (state / "program.log").read_text(encoding="utf-8").splitlines()
    assert lines[1].startswith("=== ") and lines[1].endswith(" gridsim schedule --status")
    assert lines[2].startswith("Background jobs")
    assert not data.exists()
