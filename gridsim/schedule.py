"""Background jobs: the four Windows scheduled tasks that do GridSim's regular work while its window is closed.

    GridSim Weekly    Tuesday 9:00                        gridsim weekly
    GridSim Predict   Friday 19:00                        gridsim predict-week
    GridSim Scan      every day, every 30 min, 8:00-24:00   gridsim scan --notify
    GridSim Watch     every day, every 10 min, 8:05-24:00   gridsim watch --near --notify

They are registered for the signed-in user, without administrator rights, from task XML
("schtasks /Create /TN name /XML file /F"), and run this GridSim's windowless Python (installed.pythonw_exe())
in installed.program_dir(). XML instead of schtasks flags: no quoting of the command line, no 262-character limit,
and the laptop settings (run on battery, catch up a missed run) are part of the task from the first moment.

    status()    which of the four exist, are enabled, and run THIS GridSim (never raises)
    install()   create or replace all four                      "Turn background jobs on"
    remove()    delete the four (a missing one is fine)         "Turn them off"
    repoint()   only for tasks that already exist: re-create those that run another Python (an older GridSim
                folder) or that an installer run switched off. Never creates a task the user did not have.

Rules kept throughout, because none of this can be tried outside Windows: every schtasks call is hidden, has a
timeout, and is judged by its return code or by the task XML ("schtasks /Query /TN name /XML") — never by
Windows' own message, which is translated. On anything but Windows, status() says so and the other three raise
ScheduleError with a sentence that can be shown as it is.

What was done is written to schedule.log in the program's state folder (installed.state_dir()), and the XML files
that were registered are kept in its "tasks" folder. Nothing here reads or writes the user's data folder.
"""
from __future__ import annotations

import codecs
import datetime as dt
import ntpath
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import PureWindowsPath
from xml.sax.saxutils import escape

from . import installed

NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW: no console window flashes when the parent is pythonw.exe
TIMEOUT = 30                    # seconds for one schtasks / whoami call
NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
NOT_WINDOWS = ("Background jobs use the Windows Task Scheduler, so they exist only on Windows. "
               "On this computer GridSim does what is due each time you start it.")

# What each task runs and when. "start" is a local time. Scan and Watch repeat inside a daily window ("every" for
# "for") and are registered with this very date, which lies in the past, so they take their next turn today. For
# the two weekly jobs only the weekday and the time of "start" count: see _start_boundary().
JOBS = {
    "GridSim Weekly": {
        "args": "-m gridsim weekly", "start": "2026-01-06T09:00:00", "day": "Tuesday", "limit": "PT4H",
        "when": "Tuesday 9:00",
        "what": "The weekly routine: refresh the data, grade last week, run the check-up and lock in the new week."},
    "GridSim Predict": {
        "args": "-m gridsim predict-week", "start": "2026-01-02T19:00:00", "day": "Friday", "limit": "PT4H",
        "when": "Friday 19:00",
        "what": "Fresh locked-in predictions after the final injury reports."},
    "GridSim Scan": {
        "args": "-m gridsim scan --notify", "start": "2026-01-01T08:00:00", "every": "PT30M", "for": "PT16H",
        "limit": "PT1H", "when": "Every day, every 30 minutes, 8:00 to midnight",
        "what": "Compare Kalshi prices with the sims and save the prices for grading (read-only)."},
    "GridSim Watch": {
        "args": "-m gridsim watch --near --notify", "start": "2026-01-01T08:05:00", "every": "PT10M",
        "for": "PT15H55M", "limit": "PT1H", "when": "Every day, every 10 minutes, 8:05 to midnight",
        "what": "The position watch (read-only). It only does something if you entered positions and their game "
                "kicks off within 3 hours."},
}
TASK_NAMES = installed.TASK_NAMES

# How the XML file is written, in the order tried: (file encoding, with the <UserId> line). The first is what Task
# Scheduler itself exports; the others are only tried when Windows refuses it.
_VARIANTS = (("utf-16", True), ("utf-16", False), ("utf-8", True))
_works: tuple | None = None     # the variant Windows accepted in this process: tried first for the next task


class ScheduleError(RuntimeError):
    """The background jobs could not be changed. str(e) is a plain sentence for the user."""


# ---- the task definition --------------------------------------------------------------------------------

def _start_boundary(job: dict, now: dt.datetime | None = None) -> str:
    """<StartBoundary> (local time). The Tuesday / Friday jobs catch up a missed run, and Task Scheduler can take
    a start that already lies in the past for a missed one: registered with a past date they could run the whole
    weekly routine a moment after every "turn on" and after every update, next to the GridSim that is just
    starting. So they get their NEXT turn as the start; a turn missed after that is still caught up. The repeating
    jobs do not catch up, and with their past date they simply carry on at their next turn today."""
    if "day" not in job:
        return job["start"]
    first = dt.datetime.fromisoformat(job["start"])
    now = now or dt.datetime.now()
    turn = now.replace(hour=first.hour, minute=first.minute, second=0, microsecond=0)
    turn += dt.timedelta(days=(first.weekday() - turn.weekday()) % 7)
    if turn <= now:
        turn += dt.timedelta(days=7)
    return turn.strftime("%Y-%m-%dT%H:%M:%S")


def task_xml(name: str, pythonw: str | None = None, workdir: str | None = None, user: str | None = None,
             declaration: bool = True, now: dt.datetime | None = None) -> str:
    """The Task Scheduler XML for one of the four jobs. user: the account's SID (or DOMAIN\\name); None leaves the
    <UserId> line out, which registers the task for whoever runs schtasks. now: the moment of registering (for
    the start of the weekly jobs, see _start_boundary)."""
    job = JOBS[name]
    start = _start_boundary(job, now)
    pythonw = str(pythonw or installed.pythonw_exe()).strip().strip('"')
    workdir = str(workdir or installed.program_dir()).strip().strip('"')
    repeats = "every" in job
    if repeats:
        trigger = (f"      <Repetition>\n        <Interval>{job['every']}</Interval>\n"
                   f"        <Duration>{job['for']}</Duration>\n"
                   "        <StopAtDurationEnd>false</StopAtDurationEnd>\n      </Repetition>\n"
                   f"      <StartBoundary>{start}</StartBoundary>\n      <Enabled>true</Enabled>\n"
                   "      <ScheduleByDay>\n        <DaysInterval>1</DaysInterval>\n      </ScheduleByDay>\n")
    else:
        trigger = (f"      <StartBoundary>{start}</StartBoundary>\n      <Enabled>true</Enabled>\n"
                   f"      <ScheduleByWeek>\n        <DaysOfWeek>\n          <{job['day']} />\n        </DaysOfWeek>\n"
                   "        <WeeksInterval>1</WeeksInterval>\n      </ScheduleByWeek>\n")
    # The Tuesday / Friday jobs catch up a run missed while the PC was off or asleep and may wake it; the
    # repeating ones do neither (their next turn comes within minutes anyway).
    catch_up = "false" if repeats else "true"
    user_line = f"      <UserId>{escape(user)}</UserId>\n" if user else ""
    return (
        ('<?xml version="1.0" encoding="UTF-16"?>\n' if declaration else "")
        + f'<Task version="1.2" xmlns="{NAMESPACE}">\n'
        "  <RegistrationInfo>\n"
        f"    <Description>{escape(job['what'])}</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n    <CalendarTrigger>\n" + trigger + "    </CalendarTrigger>\n  </Triggers>\n"
        '  <Principals>\n    <Principal id="Author">\n' + user_line +
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        f"    <StartWhenAvailable>{catch_up}</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        f"    <WakeToRun>{catch_up}</WakeToRun>\n"
        f"    <ExecutionTimeLimit>{job['limit']}</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n    <Exec>\n'
        # the program in quotes (as Task Scheduler writes a path with a space), the folder without (quotes break it)
        f"      <Command>{escape(chr(34) + pythonw + chr(34))}</Command>\n"
        f"      <Arguments>{escape(job['args'])}</Arguments>\n"
        f"      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>\n"
        "    </Exec>\n  </Actions>\n"
        "</Task>\n")


def _split_command(command: str, arguments: str) -> tuple[str, str]:
    """(program without quotes, arguments). A task made with "schtasks /TR" may carry both in <Command>."""
    command, arguments = (command or "").strip(), " ".join((arguments or "").split())
    if command.startswith('"') and '"' in command[1:]:
        end = command.index('"', 1)
        command, rest = command[1:end], command[end + 1:].strip()
    else:
        m = re.match(r"(?i)(.+?\.exe)\s+(.*)$", command)
        command, rest = (m.group(1), m.group(2)) if m else (command.strip('"'), "")
    return command.strip(), arguments or " ".join(rest.split())


def _parse(text: str) -> dict | None:
    """Command, arguments, folder and the Enabled switch from "schtasks /Query /XML" output; None if unreadable."""
    start, end = text.find("<Task"), text.rfind("</Task>")
    if start < 0 or end < start:
        return None
    try:
        root = ET.fromstring(text[start:end + len("</Task>")])      # whatever comes before or after is not ours
    except (ET.ParseError, ValueError):
        return None
    found: dict[str, str] = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "Settings":
            for child in el:
                if child.tag.rsplit("}", 1)[-1] == "Enabled":
                    found["enabled"] = (child.text or "").strip().lower()
        elif tag == "Exec" and "command" not in found:
            for child in el:
                found[child.tag.rsplit("}", 1)[-1].lower()] = child.text or ""
    command, arguments = _split_command(found.get("command", ""), found.get("arguments", ""))
    return {"command": command, "arguments": arguments, "workdir": found.get("workingdirectory", "").strip(),
            "enabled": found.get("enabled", "true") != "false"}


def _norm(path: str) -> str:
    return str(path).strip().strip('"').replace("/", "\\").rstrip("\\").lower()


def _same_path(theirs: str, ours: str) -> bool:
    """Is the path schtasks printed (theirs) the path ours? Quotes, slashes and case do not count. schtasks prints
    in the console's code page, so a letter outside it (in a user name, say) can come back as "?" or as other
    letters: where OUR path has such letters, anything is accepted inside that one folder name."""
    a, b = _norm(theirs), _norm(ours)
    if a == b or not a or not b:
        return a == b and bool(a)
    pattern = "".join(re.escape(part) if part.isascii() else r"[^\\]*" for part in re.split(r"([^\x00-\x7f]+)", b))
    return re.fullmatch(pattern, a) is not None


def _install_folder(command: str) -> str:
    """The GridSim folder an old task ran from: <folder>\\.venv\\Scripts\\pythonw.exe (a source folder) or
    <folder>\\python\\pythonw.exe (the installed program); otherwise the folder of the program itself."""
    d = PureWindowsPath(command.strip().strip('"')).parent
    if d.name.lower() == "scripts" and len(d.parts) >= 3:
        return str(d.parent.parent)
    if d.name.lower() == "python" and len(d.parts) >= 2:
        return str(d.parent)
    return str(d)


# ---- talking to Windows ---------------------------------------------------------------------------------

def supported() -> bool:
    return sys.platform == "win32"


def _require_windows() -> None:
    if not supported():
        raise ScheduleError(NOT_WINDOWS)


def _system32(exe: str) -> str:
    return ntpath.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", exe)


def _decode(raw) -> str:
    if isinstance(raw, str):
        return raw
    raw = raw or b""
    if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return raw.decode("utf-16", "replace")
    if raw[1:2] == b"\x00" and raw[3:4] == b"\x00":          # UTF-16 without its mark
        return raw.decode("utf-16-le", "replace")
    for enc in ("oem", "utf-8"):                # "oem" (the console's code page) exists only on Windows
        try:
            return raw.decode(enc, "replace")
        except LookupError:
            continue
    return raw.decode("latin-1")


def _log(line: str) -> None:
    try:
        p = installed.state_dir() / "schedule.log"
        mode = "w" if p.exists() and p.stat().st_size > 300_000 else "a"      # small: start over when it grows
        with open(p, mode, encoding="utf-8", errors="backslashreplace") as fh:
            fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass


def _one_line(text: str, limit: int = 300) -> str:
    return " ".join((text or "").split())[:limit]


def _run(exe: str, *args: str, log: bool = True) -> tuple[int, str]:
    """Run a Windows tool hidden, with a timeout. Returns (return code, its output as text)."""
    try:
        p = subprocess.run([_system32(exe), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, creationflags=NO_WINDOW, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        _log(f"{exe} {' '.join(args)} -> no answer in {TIMEOUT} s")
        raise ScheduleError(f"Windows did not answer within {TIMEOUT} seconds when GridSim asked about its "
                            "background jobs. Try again in a minute.") from None
    except OSError as e:
        _log(f"{exe} {' '.join(args)} -> {type(e).__name__}: {e}")
        raise ScheduleError(f"GridSim could not start the Windows tool {exe} ({e}).") from None
    out = _decode(p.stdout)
    if log:
        _log(f"{exe} {' '.join(args)} -> {p.returncode} {_one_line(out)}")
    return p.returncode, out


def _user_id() -> str | None:
    """The signed-in account for <UserId>: its SID, else DOMAIN\\name, else None (the line is left out)."""
    try:
        rc, out = _run("whoami.exe", "/user", "/fo", "csv", "/nh", log=False)
        m = re.search(r"S-1-\d+(?:-\d+)+", out) if rc == 0 else None
        if m:
            return m.group(0)
    except ScheduleError:
        pass
    name, domain = os.environ.get("USERNAME", "").strip(), os.environ.get("USERDOMAIN", "").strip()
    return (f"{domain}\\{name}" if domain else name) or None


def _target() -> tuple[str, str]:
    """(the windowless Python the tasks run, the folder they start in)."""
    pyw = installed.pythonw_exe()
    if PureWindowsPath(pyw).name.lower() != "pythonw.exe":
        again = "Install GridSim again" if installed.is_installed() else "Run Setup GridSim again"
        raise ScheduleError(f"GridSim could not find pythonw.exe next to its Python ({pyw}); without it the "
                            f"background jobs would open a window every time they run. {again}, then try once more.")
    return pyw, str(installed.program_dir())


def _register(name: str, pythonw: str, workdir: str, user: str | None) -> str | None:
    """Create or replace one task. None on success, else what Windows answered to the first attempt."""
    global _works
    first_answer, tried = None, set()
    folder = installed.state_dir() / "tasks"
    folder.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now()
    for variant in ((_works,) if _works else ()) + _VARIANTS:
        enc, with_user = variant
        xml = task_xml(name, pythonw, workdir, user if with_user else None, declaration=(enc == "utf-16"), now=now)
        if (enc, xml) in tried:
            continue
        tried.add((enc, xml))
        f = folder / (name + ".xml")
        f.write_bytes(codecs.BOM_UTF16_LE + xml.encode("utf-16-le") if enc == "utf-16" else xml.encode("utf-8"))
        rc, out = _run("schtasks.exe", "/Create", "/TN", name, "/XML", str(f), "/F")
        if rc == 0:
            _works = variant
            return None
        first_answer = out if first_answer is None else first_answer
    return first_answer or ""


def _query(name: str) -> dict:
    """One task as Windows has it. state: on | off (not there) | disabled | other (another Python or arguments)."""
    rc, out = _run("schtasks.exe", "/Query", "/TN", name, "/XML", log=False)
    t = {"name": name, "exists": rc == 0, "enabled": False, "here": False, "current": False, "command": "",
         "arguments": "", "workdir": "", "state": "off", "detail": "not set up"}
    if rc != 0:
        return t
    parsed = _parse(out)
    if parsed is None:                          # there, but unreadable: treat as not ours, so it gets replaced
        _log(f"schtasks /Query /TN {name} /XML -> 0, but not readable as task XML: {_one_line(out)}")
        t.update(enabled=True, state="other", detail="set up, but GridSim could not read its settings")
        return t
    t.update(parsed)
    t["here"] = _same_path(t["command"], installed.pythonw_exe())
    t["current"] = t["here"] and t["arguments"] == JOBS[name]["args"]
    if not t["enabled"]:
        t.update(state="disabled", detail="switched off in Windows")
    elif t["current"]:
        t.update(state="on", detail="on")
    elif t["here"]:
        t.update(state="other", detail="set up by another GridSim version")
    else:
        where = _install_folder(t["command"]) if t["command"] else ""
        t.update(state="other", detail="runs from another GridSim folder" + (f" ({where})" if where else ""))
    return t


def _failure(doing: str, failed: dict[str, str]) -> str:
    names = ", ".join(f'"{n}"' for n in failed)
    said = next((_one_line(v) for v in failed.values() if _one_line(v)), "")
    return (f"Windows would not {doing} the background job{'s' if len(failed) > 1 else ''} {names}."
            + (f" Windows said: {said}" if said else ""))


def _remember_old(folder: str) -> None:
    """Keep the folder of the GridSim the tasks ran before (for "bring in data from an old GridSim folder")."""
    try:
        p = installed.state_dir() / "old_install.txt"
        try:
            known = p.read_text(encoding="utf-8").strip() == folder
        except (OSError, ValueError):           # not there yet, or not readable: write it
            known = False
        if not known:
            p.write_text(folder + "\n", encoding="utf-8")
    except OSError:
        pass


def old_install() -> str | None:
    """The folder of the GridSim the background jobs ran from before this one took them over (one line, written by
    repoint()), or None. The app offers it under Settings > Your data > "Bring in data from an old GridSim folder"."""
    try:
        return (installed.state_dir() / "old_install.txt").read_text(encoding="utf-8").strip() or None
    except (OSError, ValueError):
        return None


# ---- the four operations --------------------------------------------------------------------------------

def status() -> dict:
    """{"supported", "state": on | off | partial | unknown | unsupported, "message", "tasks": [one dict per job]}.
    Never raises: when Windows cannot be asked, state is "unknown" and message says why."""
    if not supported():
        return {"supported": False, "state": "unsupported", "message": NOT_WINDOWS, "tasks": []}
    try:
        tasks = [_query(n) for n in TASK_NAMES]
    except ScheduleError as e:
        return {"supported": True, "state": "unknown", "message": str(e), "tasks": []}
    except Exception as e:      # nothing unexpected may break the page that shows this
        _log(f"status failed: {type(e).__name__}: {e}")
        return {"supported": True, "state": "unknown", "tasks": [],
                "message": f"GridSim could not find out whether the background jobs are on ({type(e).__name__}: {e})."}
    on = [t for t in tasks if t["state"] == "on"]
    if len(on) == len(tasks):
        state, message = "on", "Background jobs are on."
    elif not any(t["exists"] for t in tasks):
        state, message = "off", "Background jobs are off."
    else:
        rest = "; ".join(f"{t['name']}: {t['detail']}" for t in tasks if t["state"] != "on")
        state, message = "partial", f"{len(on)} of {len(tasks)} background jobs are on. Not on: {rest}."
    return {"supported": True, "state": state, "message": message, "tasks": tasks}


def install() -> dict:
    """Create or replace all four tasks for this GridSim. Raises ScheduleError (after trying all four)."""
    _require_windows()
    pythonw, workdir = _target()
    user = _user_id()
    failed = {}
    for name in TASK_NAMES:
        answer = _register(name, pythonw, workdir, user)
        if answer is not None:
            failed[name] = answer
    _log(f"install: {len(TASK_NAMES) - len(failed)} of {len(TASK_NAMES)} set up for {pythonw}")
    if failed:
        raise ScheduleError(_failure("set up", failed))
    return {"ok": True, "tasks": list(TASK_NAMES),
            "message": "Background jobs are on: Windows now runs GridSim's four jobs by itself."}


def remove() -> dict:
    """Delete the four tasks; one that is not there is fine. Raises ScheduleError if one is still there after."""
    _require_windows()
    removed, failed = [], {}
    for name in TASK_NAMES:
        rc, out = _run("schtasks.exe", "/Delete", "/TN", name, "/F")
        if rc == 0:
            removed.append(name)
        elif _run("schtasks.exe", "/Query", "/TN", name, log=False)[0] == 0:      # refused, and still there
            failed[name] = out
    _log(f"remove: {len(removed)} deleted, {len(failed)} could not be deleted")
    if failed:
        raise ScheduleError(_failure("remove", failed))
    return {"ok": True, "removed": removed, "message": "Background jobs are off."}


def repoint() -> dict:
    """After an install or update: make the tasks the user already has run THIS GridSim again. A task that runs
    another Python, or is switched off (the installer switches them off while it copies files), is re-created;
    a task that is not there stays away. The folder of the GridSim they ran before goes to old_install.txt in
    the state folder. Only the installed program does this; a source folder leaves the tasks alone."""
    _require_windows()
    if not installed.is_installed():
        return {"ok": True, "skipped": True, "changed": [], "kept": [], "missing": [], "old_install": None,
                "message": "This GridSim runs from a source folder, so the background jobs were left as they are. "
                           "Use Settings > Schedule (or Schedule GridSim) to turn them on for this folder."}
    pythonw, workdir = _target()
    changed, kept, missing, failed = [], [], [], {}
    old, user, asked = None, None, False
    for name in TASK_NAMES:
        t = _query(name)
        if not t["exists"]:
            missing.append(name)
            continue
        if t["state"] == "on":
            kept.append(name)
            continue
        if t["command"] and not t["here"] and old is None:
            folder = _install_folder(t["command"])
            if not _same_path(folder, _install_folder(pythonw)):       # (another program in this same GridSim)
                old = folder
                _remember_old(old)                          # before the task is overwritten
        if not asked:
            user, asked = _user_id(), True
        answer = _register(name, pythonw, workdir, user)
        if answer is not None and t["current"]:             # ours already, only switched off: switch it back on
            if _run("schtasks.exe", "/Change", "/TN", name, "/ENABLE")[0] == 0:
                answer = None
        if answer is None:
            changed.append(name)
        else:
            failed[name] = answer
    _log(f"repoint: {len(changed)} changed, {len(kept)} already fine, {len(missing)} not set up, {len(failed)} failed")
    if failed:
        raise ScheduleError(_failure("update", failed))
    if changed:
        message = "Background jobs now run this GridSim: " + ", ".join(changed) + "."
    elif kept:
        message = "Background jobs already run this GridSim; nothing was changed."
    else:
        message = "No background jobs are set up, so there was nothing to change."
    return {"ok": True, "skipped": False, "changed": changed, "kept": kept, "missing": missing, "old_install": old,
            "message": message}


# ---- command line: "gridsim schedule ..." (also "python -m gridsim.schedule ...") -------------------------

def add_arguments(parser) -> None:
    g = parser.add_mutually_exclusive_group()
    for flag, text in (("install", "turn the background jobs on (create or replace all four)"),
                       ("remove", "turn them off (delete the four tasks)"),
                       ("repoint", "after an update: make the jobs you already have run this GridSim"),
                       ("status", "show whether they are on (the default)")):
        g.add_argument("--" + flag, dest="action", action="store_const", const=flag, help=text)
    parser.set_defaults(action="status")


def run(action: str) -> int:
    """Do one action and print one plain result. 0 = done, 1 = not done (the reason is printed, no traceback)."""
    try:
        if action == "status":
            st = status()
            print(st["message"])
            for t in st["tasks"]:
                print(f"  {t['name']:<16} {JOBS[t['name']]['when']:<46} {t['detail']}")
            return 1 if st["state"] == "unknown" else 0
        res = {"install": install, "remove": remove, "repoint": repoint}[action]()
        print(res["message"])
        return 0
    except ScheduleError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:      # anything unexpected: still one line and a non-zero code, the details in the log
        import traceback
        _log(f"{action} failed:\n{traceback.format_exc()}")
        print(f"The background jobs could not be changed ({type(e).__name__}: {e}).", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="gridsim schedule", description="GridSim's background jobs "
                                     "(Windows scheduled tasks): turn them on or off, or show whether they are on.")
    add_arguments(parser)
    return run(parser.parse_args(argv).action)


if __name__ == "__main__":
    sys.exit(main())
