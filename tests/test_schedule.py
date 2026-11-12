"""Background jobs (Windows scheduled tasks): the task XML, what install / remove / repoint / status decide, the
command line's exit codes and the Settings > Schedule tab. Nothing here runs on Windows: schtasks.exe and whoami.exe
are a stand-in (subprocess.run is replaced), so these tests pin down what GridSim asks of Windows and what it
concludes from the answers — not that Windows accepts it. That part is the installer workflow's job."""
from __future__ import annotations

import datetime as dt
import ntpath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from gridsim import installed, schedule

ROOT = Path(__file__).resolve().parent.parent
NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
HERE = r"C:\Users\Jeff D\AppData\Local\Programs\GridSim"
PYW = HERE + r"\python\pythonw.exe"
PROGRAM = HERE + r"\program"
SID = "S-1-5-21-1004336348-1177238915-682003330-1001"
OLD_VENV = r"C:\Gridsim\.venv\Scripts\pythonw.exe"
THURSDAY = dt.datetime(2026, 11, 12, 14, 30)


def parse(xml: str) -> ET.Element:
    return ET.fromstring(xml[xml.index("<Task"):])


def text(root: ET.Element, path: str) -> str | None:
    el = root.find("/".join(NS + p for p in path.split("/")))
    return None if el is None else (el.text or "")


def old_task(command: str, arguments: str = "-m gridsim weekly", enabled: bool = True) -> str:
    """A task as "schtasks /Create ... /TR" (the old Schedule GridSim.bat) leaves it and "/Query /XML" prints it."""
    args = f"<Arguments>{arguments}</Arguments>" if arguments else ""
    return ('<?xml version="1.0" encoding="UTF-16"?>\r\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\r\n'
            "  <RegistrationInfo><Date>2026-09-01T10:00:00</Date><Author>DESKTOP-1\\jeff</Author>"
            "<URI>\\GridSim Weekly</URI></RegistrationInfo>\r\n"
            "  <Triggers><CalendarTrigger><StartBoundary>2026-09-01T09:00:00</StartBoundary><Enabled>true</Enabled>"
            "<ScheduleByWeek><DaysOfWeek><Tuesday /></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
            "</CalendarTrigger></Triggers>\r\n"
            f'  <Principals><Principal id="Author"><UserId>{SID}</UserId><LogonType>InteractiveToken</LogonType>'
            "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\r\n"
            "  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
            "<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>"
            f"<Enabled>{'true' if enabled else 'false'}</Enabled><Hidden>false</Hidden></Settings>\r\n"
            f'  <Actions Context="Author"><Exec><Command>{command}</Command>{args}</Exec></Actions>\r\n'
            "</Task>\r\n")


class Answer:
    def __init__(self, returncode: int, stdout: bytes = b""):
        self.returncode, self.stdout = returncode, stdout


class FakeWindows:
    """Stands in for schtasks.exe and whoami.exe. Keeps the task XML by name, as Task Scheduler would."""

    def __init__(self):
        self.tasks: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.options: list[dict] = []
        self.files: list[tuple[str, bytes]] = []        # (task name, the bytes of the XML file handed to /Create)
        self.refuse_create = lambda name, raw: False
        self.refuse_delete: set[str] = set()
        self.refuse_enable = False
        self.whoami = f'"desktop-1\\jeff","{SID}"\r\n'.encode()
        self.hang: str | None = None                    # a verb that never answers

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        self.options.append(kw)
        tool, args = ntpath.basename(argv[0]).lower(), list(argv[1:])
        assert ntpath.dirname(argv[0]).lower().endswith("system32"), "Windows tools are started by their full path"
        if tool == "whoami.exe":
            return Answer(0 if self.whoami else 1, self.whoami)
        assert tool == "schtasks.exe", argv
        verb, name = args[0], args[args.index("/TN") + 1]
        if verb == self.hang:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        if verb == "/Create":
            assert "/F" in args, "without /F schtasks asks Y/N when the task exists"
            raw = Path(args[args.index("/XML") + 1]).read_bytes()
            self.files.append((name, raw))
            if self.refuse_create(name, raw):
                return Answer(1, b"ERROR: Access is denied.\r\n")
            self.tasks[name] = raw[2:].decode("utf-16-le") if raw[:2] == b"\xff\xfe" else raw.decode("utf-8")
            return Answer(0, b"SUCCESS: The scheduled task has successfully been created.\r\n")
        if verb == "/Query":
            if name not in self.tasks:
                return Answer(1, b"ERROR: The system cannot find the file specified.\r\n")
            return Answer(0, self.tasks[name].encode("utf-8") if "/XML" in args else b"\r\nFolder: \\\r\n")
        if verb == "/Delete":
            assert "/F" in args
            if name not in self.tasks:
                return Answer(1, b"ERROR: The system cannot find the file specified.\r\n")
            if name in self.refuse_delete:
                return Answer(1, b"ERROR: Access is denied.\r\n")
            del self.tasks[name]
            return Answer(0, b"SUCCESS\r\n")
        if verb == "/Change":
            if name not in self.tasks or self.refuse_enable:
                return Answer(1, b"ERROR\r\n")
            self.switch(name, "/ENABLE" in args)
            return Answer(0, b"SUCCESS\r\n")
        raise AssertionError(f"unexpected call {argv}")

    def switch(self, name: str, on: bool) -> None:
        """What "schtasks /Change /ENABLE | /DISABLE" does: the Enabled line of <Settings>."""
        self.tasks[name] = re.sub(r"(<Settings>.*?<Enabled>)\w+(</Enabled>)", rf"\g<1>{'true' if on else 'false'}\g<2>",
                                  self.tasks[name], flags=re.S)

    def did(self, verb: str) -> list[str]:
        """The task names a verb was used on, in order."""
        return [c[c.index("/TN") + 1] for c in self.calls if len(c) > 1 and c[1] == verb]


@pytest.fixture()
def win(tmp_path, monkeypatch):
    """This GridSim as the installed Windows program, with a stand-in for Windows' own tools."""
    monkeypatch.setenv("GRIDSIM_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(schedule, "supported", lambda: True)
    monkeypatch.setattr(schedule, "_works", None)
    monkeypatch.setattr(installed, "pythonw_exe", lambda: PYW)
    monkeypatch.setattr(installed, "program_dir", lambda: PROGRAM)
    monkeypatch.setattr(installed, "is_installed", lambda: True)
    fake = FakeWindows()
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


# ---- the task XML ---------------------------------------------------------------------------------------

def test_the_four_jobs_are_the_ones_schedule_gridsim_bat_sets_up():
    assert tuple(schedule.JOBS) == installed.TASK_NAMES == schedule.TASK_NAMES
    bat = (ROOT / "Schedule GridSim.bat").read_text(encoding="utf-8")
    found = re.findall(r'schtasks /Create /F /TN "([^"]+)" .*?/ST (\d\d:\d\d).*?/TR "\\"!PYW!\\" ([^"]+)"', bat)
    assert len(found) == 4
    for name, start, arguments in found:
        job = schedule.JOBS[name]
        assert job["args"] == arguments
        assert job["start"].endswith(f"T{start}:00")
    for name, day in (("GridSim Weekly", "Tuesday"), ("GridSim Predict", "Friday")):
        when = dt.datetime.fromisoformat(schedule.JOBS[name]["start"])
        assert when.strftime("%A") == day == schedule.JOBS[name]["day"] and when < dt.datetime(2026, 11, 1)


def test_the_weekly_job_runs_tuesday_at_nine_on_battery_too_and_catches_up_a_missed_run():
    xml = schedule.task_xml("GridSim Weekly", PYW, PROGRAM, SID, now=THURSDAY)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    root = parse(xml)
    assert root.tag == NS + "Task" and root.get("version") == "1.2"
    assert text(root, "Actions/Exec/Command") == f'"{PYW}"'               # in quotes: the path has a space
    assert text(root, "Actions/Exec/Arguments") == "-m gridsim weekly"
    assert text(root, "Actions/Exec/WorkingDirectory") == PROGRAM         # never in quotes
    assert root.find(NS + "Actions").get("Context") == "Author"
    trig = root.find(f"{NS}Triggers/{NS}CalendarTrigger")
    assert len(root.find(NS + "Triggers")) == 1
    assert text(trig, "StartBoundary") == "2026-11-17T09:00:00" and text(trig, "Enabled") == "true"   # the next Tuesday
    assert [d.tag for d in trig.find(f"{NS}ScheduleByWeek/{NS}DaysOfWeek")] == [NS + "Tuesday"]
    assert text(trig, "ScheduleByWeek/WeeksInterval") == "1"
    assert trig.find(NS + "Repetition") is None
    want = {"DisallowStartIfOnBatteries": "false", "StopIfGoingOnBatteries": "false", "StartWhenAvailable": "true",
            "WakeToRun": "true", "Enabled": "true", "MultipleInstancesPolicy": "IgnoreNew", "RunOnlyIfIdle": "false",
            "RunOnlyIfNetworkAvailable": "false", "ExecutionTimeLimit": "PT4H", "Hidden": "false",
            "AllowStartOnDemand": "true"}
    assert {k: text(root, "Settings/" + k) for k in want} == want
    who = root.find(f"{NS}Principals/{NS}Principal")
    assert who.get("id") == "Author" and text(who, "UserId") == SID
    assert text(who, "LogonType") == "InteractiveToken" and text(who, "RunLevel") == "LeastPrivilege"
    # the order Task Scheduler itself writes (its schema is strict about it)
    assert [el.tag[len(NS):] for el in root] == ["RegistrationInfo", "Triggers", "Principals", "Settings", "Actions"]


def test_the_friday_job_locks_in_at_seven_in_the_evening():
    root = parse(schedule.task_xml("GridSim Predict", PYW, PROGRAM, SID, now=THURSDAY))
    trig = root.find(f"{NS}Triggers/{NS}CalendarTrigger")
    assert text(trig, "StartBoundary") == "2026-11-13T19:00:00"                 # tomorrow, Friday
    assert [d.tag for d in trig.find(f"{NS}ScheduleByWeek/{NS}DaysOfWeek")] == [NS + "Friday"]
    assert text(root, "Actions/Exec/Arguments") == "-m gridsim predict-week"
    assert text(root, "Settings/StartWhenAvailable") == "true" and text(root, "Settings/WakeToRun") == "true"


@pytest.mark.parametrize("now, weekly, predict", [
    ("2026-11-12T14:30:00", "2026-11-17T09:00:00", "2026-11-13T19:00:00"),      # a Thursday afternoon
    ("2026-11-17T08:59:59", "2026-11-17T09:00:00", "2026-11-20T19:00:00"),      # Tuesday, a second before nine
    ("2026-11-17T09:00:00", "2026-11-24T09:00:00", "2026-11-20T19:00:00"),      # Tuesday at nine: that turn is gone
    ("2026-11-17T10:00:00", "2026-11-24T09:00:00", "2026-11-20T19:00:00"),      # Tuesday at ten
    ("2026-11-13T19:00:30", "2026-11-17T09:00:00", "2026-11-20T19:00:00"),      # Friday, just after seven
    ("2026-12-29T12:00:00", "2027-01-05T09:00:00", "2027-01-01T19:00:00")])     # over the turn of the year
def test_the_two_weekly_jobs_start_with_their_next_turn_so_that_turning_them_on_does_not_run_them_at_once(
        now, weekly, predict):
    # A job that catches up missed runs and is registered with a start in the past can be started by Windows
    # right away, as "missed": then every "turn on" and every update would run the weekly routine at once.
    now = dt.datetime.fromisoformat(now)
    for name, want, day in (("GridSim Weekly", weekly, "Tuesday"), ("GridSim Predict", predict, "Friday")):
        trig = parse(schedule.task_xml(name, PYW, PROGRAM, SID, now=now)).find(f"{NS}Triggers/{NS}CalendarTrigger")
        start = dt.datetime.fromisoformat(text(trig, "StartBoundary"))
        assert text(trig, "StartBoundary") == want
        assert now < start <= now + dt.timedelta(days=7) and start.strftime("%A") == day
        assert text(parse(schedule.task_xml(name, PYW, PROGRAM, SID, now=now)), "Settings/StartWhenAvailable") == "true"
    # without a given moment it is the clock's: still in the future
    trig = parse(schedule.task_xml("GridSim Weekly", PYW, PROGRAM, SID)).find(f"{NS}Triggers/{NS}CalendarTrigger")
    assert dt.datetime.fromisoformat(text(trig, "StartBoundary")) > dt.datetime.now()
    # the repeating jobs keep their fixed start in the past (they do not catch up, and carry on today)
    for name in ("GridSim Scan", "GridSim Watch"):
        trig = parse(schedule.task_xml(name, PYW, PROGRAM, SID, now=now)).find(f"{NS}Triggers/{NS}CalendarTrigger")
        assert text(trig, "StartBoundary") == schedule.JOBS[name]["start"] < "2026-11"


@pytest.mark.parametrize("name, start, every, span, arguments", [
    ("GridSim Scan", "2026-01-01T08:00:00", "PT30M", "PT16H", "-m gridsim scan --notify"),
    ("GridSim Watch", "2026-01-01T08:05:00", "PT10M", "PT15H55M", "-m gridsim watch --near --notify")])
def test_the_price_check_and_the_position_watch_repeat_every_day_until_midnight(name, start, every, span, arguments):
    root = parse(schedule.task_xml(name, PYW, PROGRAM, SID))
    trig = root.find(f"{NS}Triggers/{NS}CalendarTrigger")
    assert [el.tag[len(NS):] for el in trig] == ["Repetition", "StartBoundary", "Enabled", "ScheduleByDay"]
    assert text(trig, "StartBoundary") == start and text(trig, "ScheduleByDay/DaysInterval") == "1"
    assert text(trig, "Repetition/Interval") == every and text(trig, "Repetition/Duration") == span
    assert text(trig, "Repetition/StopAtDurationEnd") == "false"
    assert text(root, "Actions/Exec/Arguments") == arguments
    # on battery too; but no catching up and no waking the PC for a job whose next turn is minutes away, and a
    # run that hangs is ended after an hour instead of blocking the next ones for days
    want = {"DisallowStartIfOnBatteries": "false", "StopIfGoingOnBatteries": "false", "StartWhenAvailable": "false",
            "WakeToRun": "false", "ExecutionTimeLimit": "PT1H", "MultipleInstancesPolicy": "IgnoreNew"}
    assert {k: text(root, "Settings/" + k) for k in want} == want
    hours = re.fullmatch(r"PT(\d+)H(?:(\d+)M)?", span)
    end = dt.datetime.fromisoformat(start) + dt.timedelta(hours=int(hours[1]), minutes=int(hours[2] or 0))
    assert end.strftime("%H:%M") == "00:00"


def test_a_folder_name_with_an_ampersand_or_an_accent_survives_the_xml():
    pyw = r"C:\Users\Tom & Jörg <2>\AppData\Local\Programs\GridSim\python\pythonw.exe"
    folder = r"C:\Users\Tom & Jörg <2>\AppData\Local\Programs\GridSim\program"
    xml = schedule.task_xml("GridSim Scan", pyw, folder, r"DESKTOP-1\Tom & Jörg")
    assert "Tom &amp; J" in xml and "<2>" not in xml
    root = parse(xml)
    assert text(root, "Actions/Exec/Command") == f'"{pyw}"'
    assert text(root, "Actions/Exec/WorkingDirectory") == folder
    assert text(root, "Principals/Principal/UserId") == r"DESKTOP-1\Tom & Jörg"
    # a path that already came in quotes is not quoted twice
    assert text(parse(schedule.task_xml("GridSim Scan", f'"{pyw}"', folder)), "Actions/Exec/Command") == f'"{pyw}"'


def test_without_a_known_account_the_user_line_is_left_out_and_the_file_can_go_without_its_first_line():
    xml = schedule.task_xml("GridSim Watch", PYW, PROGRAM, None, declaration=False)
    assert xml.startswith("<Task ") and "<UserId>" not in xml
    who = parse(xml).find(f"{NS}Principals/{NS}Principal")
    assert [el.tag[len(NS):] for el in who] == ["LogonType", "RunLevel"]


def test_the_account_is_the_sid_windows_reports_else_the_login_name_else_nothing(win, monkeypatch):
    assert schedule._user_id() == SID
    assert win.calls[-1][1:] == ["/user", "/fo", "csv", "/nh"]
    win.whoami = b""                                               # whoami fails or prints nothing usable
    monkeypatch.setenv("USERNAME", "jeff")
    monkeypatch.setenv("USERDOMAIN", "DESKTOP-1")
    assert schedule._user_id() == r"DESKTOP-1\jeff"
    monkeypatch.delenv("USERDOMAIN")
    assert schedule._user_id() == "jeff"
    monkeypatch.delenv("USERNAME")
    assert schedule._user_id() is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no whoami")))
    assert schedule._user_id() is None                             # never an error: the line is simply left out


# ---- install --------------------------------------------------------------------------------------------

def test_turning_the_jobs_on_registers_all_four_for_this_gridsim_hidden_and_with_a_timeout(win, tmp_path):
    res = schedule.install()
    assert res["ok"] and res["tasks"] == list(installed.TASK_NAMES) and "on" in res["message"]
    assert win.did("/Create") == list(installed.TASK_NAMES) and set(win.tasks) == set(installed.TASK_NAMES)
    for kw in win.options:                                          # every call: no window, no waiting forever
        assert kw["creationflags"] == 0x08000000 and kw["timeout"] == 30 and kw["stdin"] is subprocess.DEVNULL
        assert kw["stdout"] is subprocess.PIPE and kw["stderr"] is subprocess.STDOUT and not kw.get("shell")
    create = next(c for c in win.calls if c[1] == "/Create")
    assert create[0] == r"C:\Windows\System32\schtasks.exe"
    assert create[1:4] == ["/Create", "/TN", "GridSim Weekly"] and create[4] == "/XML" and create[6] == "/F"
    assert Path(create[5]).parent == tmp_path / "state" / "tasks"   # the program's state folder, not the data folder
    name, raw = win.files[0]
    assert raw[:2] == b"\xff\xfe" and raw[2:].decode("utf-16-le").startswith('<?xml version="1.0" encoding="UTF-16"?>')
    for name in installed.TASK_NAMES:
        root = parse(win.tasks[name])
        assert text(root, "Actions/Exec/Command") == f'"{PYW}"'
        assert text(root, "Actions/Exec/Arguments") == schedule.JOBS[name]["args"]
        assert text(root, "Actions/Exec/WorkingDirectory") == PROGRAM
        assert text(root, "Principals/Principal/UserId") == SID
    for name in ("GridSim Weekly", "GridSim Predict"):             # no start in the past that Windows would catch up
        start = text(parse(win.tasks[name]), "Triggers/CalendarTrigger/StartBoundary")
        assert dt.datetime.fromisoformat(start) > dt.datetime.now()
    assert schedule.status()["state"] == "on"
    assert not (tmp_path / "GridSim Data").exists()                 # the user's data folder is not touched
    log = (tmp_path / "state" / "schedule.log").read_text(encoding="utf-8")
    assert "/Create /TN GridSim Weekly" in log and "install: 4 of 4" in log


def test_turning_them_on_again_replaces_the_tasks_of_an_older_gridsim_folder(win):
    win.tasks["GridSim Weekly"] = old_task(f'"{OLD_VENV}"')
    assert schedule.status()["state"] == "partial"
    schedule.install()
    assert schedule.status()["state"] == "on"
    assert text(parse(win.tasks["GridSim Weekly"]), "Actions/Exec/Command") == f'"{PYW}"'


def test_if_windows_refuses_the_file_it_is_offered_without_the_account_and_then_as_utf8(win):
    win.refuse_create = lambda name, raw: b"<\x00U\x00s\x00e\x00r\x00I\x00d\x00>" in raw     # no <UserId> wanted
    schedule.install()
    assert schedule.status()["state"] == "on"
    assert all("<UserId>" not in x for x in win.tasks.values())
    # found out once, on the first task; the other three are registered the working way straight away
    assert win.did("/Create") == ["GridSim Weekly", "GridSim Weekly", "GridSim Predict", "GridSim Scan", "GridSim Watch"]


def test_if_windows_wants_utf8_that_is_what_it_gets(win):
    win.refuse_create = lambda name, raw: raw[:2] == b"\xff\xfe"
    schedule.install()
    assert schedule.status()["state"] == "on"
    name, raw = win.files[-1]
    assert raw.startswith(b"<Task ") and b"<UserId>" + SID.encode() in raw
    assert len(win.did("/Create")) == 3 + 3                          # three tries for the first, one for each other


def test_when_windows_will_not_create_the_tasks_the_error_names_them_and_quotes_windows(win):
    win.refuse_create = lambda name, raw: name in ("GridSim Predict", "GridSim Watch")
    with pytest.raises(schedule.ScheduleError) as e:
        schedule.install()
    msg = str(e.value)
    assert msg.startswith('Windows would not set up the background jobs "GridSim Predict", "GridSim Watch".')
    assert "Windows said: ERROR: Access is denied." in msg and "\n" not in msg
    assert set(win.tasks) == {"GridSim Weekly", "GridSim Scan"}       # the others were still tried
    st = schedule.status()
    assert st["state"] == "partial" and st["message"].startswith("2 of 4 background jobs are on.")
    assert "GridSim Predict: not set up" in st["message"]


def test_no_tasks_are_made_for_a_python_that_would_open_a_window(win, monkeypatch):
    monkeypatch.setattr(installed, "pythonw_exe", lambda: HERE + r"\python\python.exe")   # no pythonw.exe found
    with pytest.raises(schedule.ScheduleError, match="could not find pythonw.exe"):
        schedule.install()
    assert win.calls == []


# ---- status ---------------------------------------------------------------------------------------------

def test_status_reads_the_task_xml_and_says_off_on_or_what_is_in_between(win):
    st = schedule.status()
    assert st["supported"] and st["state"] == "off" and st["message"] == "Background jobs are off."
    assert [t["name"] for t in st["tasks"]] == list(installed.TASK_NAMES)
    assert all(not t["exists"] and t["detail"] == "not set up" for t in st["tasks"])
    assert all(c[1:] == ["/Query", "/TN", c[3], "/XML"] for c in win.calls)      # asked for XML, never the text list
    schedule.install()
    st = schedule.status()
    assert st["state"] == "on" and st["message"] == "Background jobs are on."
    assert all(t["exists"] and t["enabled"] and t["here"] and t["detail"] == "on" for t in st["tasks"])
    assert st["tasks"][0]["command"] == PYW and st["tasks"][0]["workdir"] == PROGRAM
    win.switch("GridSim Scan", False)                                # what the installer does before copying files
    win.tasks["GridSim Watch"] = old_task(f'"{OLD_VENV}"', "-m gridsim watch --near --notify")
    st = schedule.status()
    by = {t["name"]: t for t in st["tasks"]}
    assert st["state"] == "partial" and st["message"].startswith("2 of 4 background jobs are on. Not on: ")
    assert by["GridSim Scan"]["state"] == "disabled" and by["GridSim Scan"]["detail"] == "switched off in Windows"
    assert by["GridSim Watch"]["state"] == "other" and not by["GridSim Watch"]["here"]
    assert by["GridSim Watch"]["detail"] == r"runs from another GridSim folder (C:\Gridsim)"


def test_tasks_made_by_schedule_gridsim_bat_for_this_same_folder_count_as_on(win, monkeypatch):
    monkeypatch.setattr(installed, "pythonw_exe", lambda: OLD_VENV)                  # a source folder's .venv
    for name in installed.TASK_NAMES:                                                # quoted command, no folder
        win.tasks[name] = old_task('"c:/GRIDSIM/.venv/Scripts/pythonw.exe"', schedule.JOBS[name]["args"])
    assert schedule.status()["state"] == "on"
    # schtasks /TR without inner quotes leaves program and arguments in one piece: still understood
    win.tasks["GridSim Weekly"] = old_task(OLD_VENV + " -m gridsim weekly", "")
    t = schedule.status()["tasks"][0]
    assert t["state"] == "on" and t["command"] == OLD_VENV and t["arguments"] == "-m gridsim weekly"
    win.tasks["GridSim Weekly"] = old_task(f'"{OLD_VENV}" -m gridsim weekly --tune', "")
    t = schedule.status()["tasks"][0]
    assert t["state"] == "other" and t["here"] and t["detail"] == "set up by another GridSim version"


def test_a_user_name_windows_cannot_print_does_not_make_the_task_look_foreign(win, monkeypatch):
    pyw = r"C:\Users\Jörg Müller\AppData\Local\Programs\GridSim\python\pythonw.exe"
    monkeypatch.setattr(installed, "pythonw_exe", lambda: pyw)
    for shown in ("J?rg M?ller", "Jorg Muller", "J\ufffdrg M\ufffdller", "JÃ¶rg MÃ¼ller", "Jörg Müller"):
        win.tasks["GridSim Weekly"] = old_task('"' + pyw.replace("Jörg Müller", shown) + '"')
        assert schedule.status()["tasks"][0]["state"] == "on", shown
    win.tasks["GridSim Weekly"] = old_task('"' + pyw.replace("Jörg Müller", r"Jorg\Muller") + '"')
    assert schedule.status()["tasks"][0]["state"] == "other"            # another folder is still another folder
    assert not schedule._same_path("", "") and not schedule._same_path("", pyw)


def test_a_task_whose_settings_cannot_be_read_is_there_but_not_ours(win):
    win.tasks["GridSim Weekly"] = "<Task><Settings><Enabled>true</Enabled>"           # cut off
    t = schedule.status()["tasks"][0]
    assert t["exists"] and t["state"] == "other" and "could not read" in t["detail"]
    assert schedule.repoint()["changed"] == ["GridSim Weekly"]                        # so repoint replaces it
    assert schedule.status()["tasks"][0]["state"] == "on"


def test_status_never_raises_when_windows_does_not_answer(win, tmp_path):
    win.hang = "/Query"
    st = schedule.status()
    assert st["supported"] and st["state"] == "unknown" and st["tasks"] == []
    assert "did not answer within 30 seconds" in st["message"]
    assert "no answer in 30 s" in (tmp_path / "state" / "schedule.log").read_text(encoding="utf-8")


def test_status_never_raises_on_anything_unexpected_either(win, tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "_parse", lambda text: 1 / 0)
    win.tasks["GridSim Weekly"] = old_task(f'"{PYW}"')
    st = schedule.status()                                               # (the Settings page shows this as it is)
    assert st["supported"] and st["state"] == "unknown" and st["tasks"] == []
    assert st["message"].startswith("GridSim could not find out whether the background jobs are on (ZeroDivisionError")
    assert "status failed: ZeroDivisionError" in (tmp_path / "state" / "schedule.log").read_text(encoding="utf-8")


def test_text_windows_prints_before_or_after_the_task_does_not_make_it_unreadable(win):
    schedule.install()
    win.tasks["GridSim Weekly"] = "\r\nINFO: something\r\n" + win.tasks["GridSim Weekly"] + "\r\n\x00\r\nmore text\r\n"
    t = schedule.status()["tasks"][0]
    assert t["state"] == "on" and t["command"] == PYW
    assert schedule.repoint()["kept"] == list(installed.TASK_NAMES)       # so it is not re-created at every start
    assert schedule._parse("</Task> <Task") is None


def test_schtasks_output_is_read_whatever_its_encoding():
    xml = old_task(f'"{OLD_VENV}"')
    for raw in (xml.encode("utf-8"), b"\xff\xfe" + xml.encode("utf-16-le"), xml.encode("utf-16-le"), xml,
                b"\xef\xbb\xbf" + xml.encode("utf-8")):
        assert schedule._parse(schedule._decode(raw))["command"] == OLD_VENV
    assert schedule._decode(None) == "" and schedule._parse("ERROR: nothing") is None


# ---- remove ---------------------------------------------------------------------------------------------

def test_turning_the_jobs_off_deletes_the_four_and_a_missing_one_is_fine(win):
    schedule.install()
    del win.tasks["GridSim Scan"]
    res = schedule.remove()
    assert res["ok"] and res["removed"] == ["GridSim Weekly", "GridSim Predict", "GridSim Watch"]
    assert win.tasks == {} and schedule.status()["state"] == "off"
    assert win.did("/Delete") == list(installed.TASK_NAMES)
    assert schedule.remove()["removed"] == []                         # nothing there: still fine


def test_a_task_windows_will_not_delete_is_reported_and_the_others_still_go(win):
    schedule.install()
    win.refuse_delete = {"GridSim Predict"}
    with pytest.raises(schedule.ScheduleError) as e:
        schedule.remove()
    assert str(e.value).startswith('Windows would not remove the background job "GridSim Predict".')
    assert "Access is denied" in str(e.value) and set(win.tasks) == {"GridSim Predict"}


# ---- repoint --------------------------------------------------------------------------------------------

def test_repoint_never_creates_a_task_the_user_did_not_have(win, tmp_path):
    res = schedule.repoint()
    assert res["ok"] and res["changed"] == [] and res["missing"] == list(installed.TASK_NAMES)
    assert win.did("/Create") == [] and win.tasks == {}
    assert "nothing to change" in res["message"]
    assert not (tmp_path / "state" / "old_install.txt").exists()
    assert schedule.old_install() is None                    # nothing to offer under Settings > Your data


def test_repoint_moves_the_tasks_of_the_old_folder_to_this_gridsim_and_remembers_that_folder(win, tmp_path):
    win.tasks["GridSim Weekly"] = old_task(f'"{OLD_VENV}"')
    win.tasks["GridSim Predict"] = old_task(f'"{OLD_VENV}"', "-m gridsim predict-week")
    res = schedule.repoint()
    assert res["changed"] == ["GridSim Weekly", "GridSim Predict"] and res["kept"] == []
    assert res["missing"] == ["GridSim Scan", "GridSim Watch"] and set(win.tasks) == {"GridSim Weekly", "GridSim Predict"}
    assert res["old_install"] == r"C:\Gridsim" and "GridSim Weekly, GridSim Predict" in res["message"]
    assert (tmp_path / "state" / "old_install.txt").read_text(encoding="utf-8") == "C:\\Gridsim\n"
    assert schedule.old_install() == r"C:\Gridsim"           # what the app offers to bring data in from
    for name in ("GridSim Weekly", "GridSim Predict"):
        root = parse(win.tasks[name])
        assert text(root, "Actions/Exec/Command") == f'"{PYW}"'
        assert text(root, "Actions/Exec/WorkingDirectory") == PROGRAM
        assert text(root, "Settings/DisallowStartIfOnBatteries") == "false"       # the old one had Windows' default
    by = {t["name"]: t["state"] for t in schedule.status()["tasks"]}
    assert by == {"GridSim Weekly": "on", "GridSim Predict": "on", "GridSim Scan": "off", "GridSim Watch": "off"}
    assert len([c for c in win.calls if "whoami" in c[0]]) == 1                    # asked once, not per task
    win.calls.clear()
    again = schedule.repoint()                                                     # a second run changes nothing
    assert again["changed"] == [] and again["kept"] == ["GridSim Weekly", "GridSim Predict"]
    assert win.did("/Create") == [] and "nothing was changed" in again["message"]


@pytest.mark.parametrize("command, folder", [
    (r"C:\dummy\pythonw.exe", r"C:\dummy"),
    (r'"D:\Old Stuff\GameSim\.venv\Scripts\pythonw.exe"', r"D:\Old Stuff\GameSim"),
    (r"C:\Users\x\AppData\Local\Programs\GridSim Old\python\pythonw.exe", r"C:\Users\x\AppData\Local\Programs\GridSim Old"),
    (r"C:\Scripts\pythonw.exe", r"C:\Scripts")])
def test_the_old_gridsim_folder_is_worked_out_from_the_old_command(win, tmp_path, command, folder):
    win.tasks["GridSim Weekly"] = old_task(command)
    assert schedule.repoint()["old_install"] == folder
    assert (tmp_path / "state" / "old_install.txt").read_text(encoding="utf-8").strip() == folder


def test_an_old_install_note_that_cannot_be_read_is_written_again(win, tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "old_install.txt").write_bytes(b"\xff\xfe\x00C\x00:")          # not UTF-8
    assert schedule.old_install() is None
    win.tasks["GridSim Weekly"] = old_task(f'"{OLD_VENV}"')
    assert schedule.repoint()["old_install"] == r"C:\Gridsim"
    assert (tmp_path / "state" / "old_install.txt").read_text(encoding="utf-8") == "C:\\Gridsim\n"


def test_another_program_of_this_same_gridsim_is_not_remembered_as_an_old_folder(win, tmp_path):
    win.tasks["GridSim Weekly"] = old_task(f'"{HERE}\\python\\python.exe"')          # python.exe, not pythonw.exe
    res = schedule.repoint()
    assert res["changed"] == ["GridSim Weekly"] and res["old_install"] is None
    assert not (tmp_path / "state" / "old_install.txt").exists()


def test_repoint_switches_tasks_back_on_that_an_installer_run_switched_off(win, tmp_path):
    schedule.install()
    for name in installed.TASK_NAMES:
        win.switch(name, False)
    win.calls.clear()
    res = schedule.repoint()
    assert res["changed"] == list(installed.TASK_NAMES) and schedule.status()["state"] == "on"
    assert win.did("/Create") == list(installed.TASK_NAMES)             # re-created, not just switched on
    assert res["old_install"] is None and not (tmp_path / "state" / "old_install.txt").exists()


def test_repoint_leaves_tasks_alone_that_are_already_right(win):
    schedule.install()
    before = dict(win.tasks)
    win.calls.clear()
    res = schedule.repoint()
    assert res["kept"] == list(installed.TASK_NAMES) and res["changed"] == [] and win.tasks == before
    assert {c[1] for c in win.calls} == {"/Query"}                       # it only looked


def test_if_a_switched_off_task_cannot_be_recreated_it_is_at_least_switched_on(win):
    schedule.install()
    win.switch("GridSim Scan", False)
    win.refuse_create = lambda name, raw: True
    res = schedule.repoint()
    assert res["changed"] == ["GridSim Scan"] and schedule.status()["state"] == "on"
    assert [c[1:] for c in win.calls if c[1] == "/Change"] == [["/Change", "/TN", "GridSim Scan", "/ENABLE"]]
    win.switch("GridSim Scan", False)
    win.refuse_enable = True
    with pytest.raises(schedule.ScheduleError, match='would not update the background job "GridSim Scan"'):
        schedule.repoint()


def test_a_task_of_the_old_folder_that_cannot_be_replaced_is_an_error_and_is_not_just_switched_on(win, tmp_path):
    win.tasks["GridSim Weekly"] = old_task(f'"{OLD_VENV}"', enabled=False)
    win.refuse_create = lambda name, raw: True
    with pytest.raises(schedule.ScheduleError) as e:
        schedule.repoint()
    assert "Access is denied" in str(e.value) and win.did("/Change") == []
    assert "Scripts" in win.tasks["GridSim Weekly"] and "<Enabled>false" in win.tasks["GridSim Weekly"]
    assert (tmp_path / "state" / "old_install.txt").read_text(encoding="utf-8").strip() == r"C:\Gridsim"


def test_a_source_folder_does_not_take_the_tasks_away_from_the_installed_program(win, monkeypatch):
    win.tasks["GridSim Weekly"] = old_task(f'"{PYW}"')
    monkeypatch.setattr(installed, "is_installed", lambda: False)
    monkeypatch.setattr(installed, "pythonw_exe", lambda: OLD_VENV)
    res = schedule.repoint()
    assert res["ok"] and res["skipped"] and res["changed"] == [] and "source folder" in res["message"]
    assert win.calls == [] and PYW in win.tasks["GridSim Weekly"]
    schedule.install()                                # the buttons do work for a source folder: the .venv's pythonw
    assert all(text(parse(x), "Actions/Exec/Command") == f'"{OLD_VENV}"' for x in win.tasks.values())


# ---- not Windows ----------------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="what happens where there is no Task Scheduler")
def test_off_windows_status_says_so_and_nothing_is_ever_started(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIDSIM_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("nothing may be started"))
    st = schedule.status()
    assert st == {"supported": False, "state": "unsupported", "message": schedule.NOT_WINDOWS, "tasks": []}
    assert "only on Windows" in st["message"]
    for act in (schedule.install, schedule.remove, schedule.repoint):
        with pytest.raises(schedule.ScheduleError, match="only on Windows"):
            act()


# ---- the command line -----------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="what happens where there is no Task Scheduler")
def test_off_windows_the_command_prints_one_line_and_fails_without_a_traceback(tmp_path, monkeypatch, capsys):
    from gridsim import cli
    monkeypatch.setenv("GRIDSIM_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for flag in ("--install", "--remove", "--repoint"):
        assert cli.main(["schedule", flag]) == 1
        out = capsys.readouterr()
        assert out.err.strip().endswith(schedule.NOT_WINDOWS) and "Traceback" not in out.err
    assert cli.main(["schedule", "--status"]) == 0 and schedule.NOT_WINDOWS in capsys.readouterr().out
    assert cli.main(["schedule"]) == 0                                 # no flag: status


def test_the_command_exits_zero_when_done_and_one_with_a_plain_line_when_not(win, tmp_path, monkeypatch, capsys):
    from gridsim import cli
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["schedule", "--install"]) == 0
    assert "Background jobs are on" in capsys.readouterr().out and len(win.tasks) == 4
    assert cli.main(["schedule", "--status"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Background jobs are on." and "GridSim Watch" in out and "Tuesday 9:00" in out
    assert cli.main(["schedule", "--repoint"]) == 0 and "nothing was changed" in capsys.readouterr().out
    assert cli.main(["schedule", "--remove"]) == 0 and win.tasks == {}
    assert capsys.readouterr().out.strip().endswith("Background jobs are off.")
    win.refuse_create = lambda name, raw: True
    assert cli.main(["schedule", "--install"]) == 1
    err = capsys.readouterr().err
    assert "Windows would not set up the background jobs" in err and "Traceback" not in err
    win.hang = "/Query"
    assert cli.main(["schedule", "--status"]) == 1                     # Windows could not be asked
    with pytest.raises(SystemExit) as e:                               # two actions at once: argparse says no
        cli.main(["schedule", "--install", "--remove"])
    assert e.value.code == 2


def test_the_module_can_be_run_on_its_own_and_an_unexpected_error_is_one_line_too(win, tmp_path, monkeypatch, capsys):
    assert schedule.main(["--install"]) == 0 and len(win.tasks) == 4
    assert schedule.main([]) == 0 and capsys.readouterr().out.count("\n") >= 5
    monkeypatch.setattr(schedule, "remove", lambda: 1 / 0)
    assert schedule.main(["--remove"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("The background jobs could not be changed (ZeroDivisionError") and err.count("\n") == 1
    assert "Traceback" in (tmp_path / "state" / "schedule.log").read_text(encoding="utf-8")   # the details: the log
    assert not (tmp_path / "GridSim Data").exists()                    # this entry point leaves the data folder alone


# ---- Settings > Schedule ----------------------------------------------------------------------------------

class FakeSchedule:
    """What the Settings tab talks to: status / install / remove with a state that the buttons change."""

    def __init__(self, monkeypatch, state="off", supported=True):
        self.state, self.supported, self.fail, self.calls = state, supported, None, []
        for name in ("status", "install", "remove"):
            monkeypatch.setattr(schedule, name, getattr(self, name))

    def status(self):
        if not self.supported:
            return {"supported": False, "state": "unsupported", "message": schedule.NOT_WINDOWS, "tasks": []}
        detail = {"on": "on", "off": "not set up", "partial": "switched off in Windows"}[self.state]
        tasks = [{"name": n, "detail": "on" if self.state == "partial" and n != "GridSim Scan" else detail}
                 for n in installed.TASK_NAMES]
        message = {"on": "Background jobs are on.", "off": "Background jobs are off.",
                   "partial": "3 of 4 background jobs are on. Not on: GridSim Scan: switched off in Windows."}[self.state]
        return {"supported": True, "state": self.state, "message": message, "tasks": tasks}

    def _act(self, what, state):
        self.calls.append(what)
        if self.fail:
            raise schedule.ScheduleError(self.fail)
        self.state = state
        return {"ok": True, "message": "done"}

    def install(self):
        return self._act("install", "on")

    def remove(self):
        return self._act("remove", "off")


def settings_page(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.setenv("GRIDSIM_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.syspath_prepend(str(ROOT / "app"))
    at = AppTest.from_file(str(ROOT / "app" / "pages" / "settings.py"), default_timeout=60)
    at.session_state["_via_app"] = True                                # (as when opened through the app)
    return at


def press(at, label):
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception


def shown(at) -> str:
    """Everything the page says in plain boxes, captions and tables."""
    parts = [el.value for kind in ("success", "info", "warning", "error", "caption", "markdown") for el in getattr(at, kind)]
    return "\n".join(parts + [el.proto.body for el in at.html])


def test_the_schedule_tab_says_whether_the_jobs_are_on_and_its_two_buttons_turn_them_on_and_off(tmp_path, monkeypatch):
    fake = FakeSchedule(monkeypatch, "off")
    at = settings_page(tmp_path, monkeypatch)
    at.run()
    assert not at.exception
    assert any("Background jobs are **off**" in el.value for el in at.info)
    page = shown(at)
    for name, job in schedule.JOBS.items():                            # what the jobs do, in words
        assert name in page and job["when"] in page
    assert "not set up" in page and "scheduled.log" in page
    assert any(m.value == "##### Last activity" for m in at.markdown)   # the old parts of the tab are still there
    assert any("console users" in e.label for e in at.expander)
    press(at, "Turn background jobs on")
    assert fake.calls == ["install"]
    assert any(el.value.startswith("Done. Background jobs are **on**") for el in at.success) and not at.error
    press(at, "Turn them off")
    assert fake.calls == ["install", "remove"]
    assert any(el.value.startswith("Done. Background jobs are **off**") for el in at.info)
    # a fresh look at the page (no button pressed) does not change anything and does not say "Done"
    asked = []
    monkeypatch.setattr(schedule, "status", lambda: asked.append(1) or fake.status())
    at.run()
    assert fake.calls == ["install", "remove"] and not any("Done" in el.value for el in at.info)
    assert any("Background jobs are **off**" in el.value for el in at.info)
    assert asked == []                 # and Windows is not asked again at every click on the page (a short memory)
    at.session_state["_sched"] = (at.session_state["_sched"][0] - 60, at.session_state["_sched"][1])
    at.run()
    assert asked == [1]                # ... but it is, once that memory is a minute old


def test_the_schedule_tab_puts_a_problem_in_plain_words_and_shows_what_is_half_set_up(tmp_path, monkeypatch):
    fake = FakeSchedule(monkeypatch, "partial")
    at = settings_page(tmp_path, monkeypatch)
    at.run()
    assert not at.exception
    warn = [el.value for el in at.warning if "background jobs are on" in el.value]
    assert warn and warn[0].startswith("3 of 4 background jobs are on. Not on: GridSim Scan: switched off in Windows.")
    assert "Press **Turn background jobs on**" in warn[0] and "switched off in Windows" in shown(at)
    fake.fail = 'Windows would not set up the background job "GridSim Scan". Windows said: ERROR: Access is denied.'
    press(at, "Turn background jobs on")
    assert [el.value for el in at.error] == [fake.fail]
    assert any("Task Scheduler" in el.value and "screenshot" in el.value for el in at.caption)
    assert not any("Done" in el.value for el in at.warning)             # still half set up, and it says so
    assert any(el.value.startswith("3 of 4") for el in at.warning)
    monkeypatch.setattr(schedule, "remove", lambda: 1 / 0)              # anything unexpected: a sentence, no crash
    press(at, "Turn them off")
    assert [el.value for el in at.error] == ["The background jobs could not be changed (ZeroDivisionError: division by zero)."]


def test_the_schedule_tab_off_windows_says_what_applies_and_has_no_buttons(tmp_path, monkeypatch):
    FakeSchedule(monkeypatch, supported=False)
    at = settings_page(tmp_path, monkeypatch)
    at.run()
    assert not at.exception
    assert any(el.value == schedule.NOT_WINDOWS for el in at.info)
    assert not [b for b in at.button if b.label in ("Turn background jobs on", "Turn them off")]
    assert any(m.value == "##### Last activity" for m in at.markdown)


def test_the_folder_gridsim_ran_from_before_is_offered_for_bringing_data_in(tmp_path, monkeypatch):
    """repoint() writes the old GridSim folder to old_install.txt; Settings > Your data fills it in as the folder to
    bring data in from - only in the installed program, and only while that folder still exists."""
    FakeSchedule(monkeypatch, "off")
    old = tmp_path / "Old GridSim"
    old.mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "old_install.txt").write_text(f"{old}\n", encoding="utf-8")

    at = settings_page(tmp_path, monkeypatch)                          # a source folder: nothing is filled in
    at.run()
    assert not at.exception
    box = next(t for t in at.text_input if t.label == "Old GridSim folder")
    assert box.value == "" and "Filled in for you" not in shown(at)

    monkeypatch.setattr(installed, "is_installed", lambda: True)       # the installed program: the folder is offered
    at = settings_page(tmp_path, monkeypatch)
    at.run()
    assert not at.exception
    box = next(t for t in at.text_input if t.label == "Old GridSim folder")
    assert box.value == str(old) and "Filled in for you" in shown(at)
    assert not next(b for b in at.button if b.label == "Bring it in").disabled

    old.rmdir()                                                        # the old folder is gone: nothing to offer
    at = settings_page(tmp_path, monkeypatch)
    at.run()
    assert not at.exception
    assert next(t for t in at.text_input if t.label == "Old GridSim folder").value == ""
