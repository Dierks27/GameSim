"""Start a program as a CHILD OF ANOTHER PROCESS and print the new process id (test 09 of the smoke test).

    python spawn_under.py <parent pid> <creation flags, e.g. 0x08000000> <program> [arguments ...]

Why: the launcher keeps the hidden server in a Windows "job" that is killed when the launcher ends. Whatever the
server starts lands in that job too, unless it is started with CREATE_BREAKAWAY_FROM_JOB (the overnight tuning run
is). To test both cases from outside, this script starts a process whose parent is the server: Windows then treats
it exactly like something the server started itself (same job, same breakaway rules).

Exit code 0 and the process id on the last line of stdout when it worked. Otherwise a line that starts with
"spawn_under:" and exit code 5 for "access denied" (which is what Windows answers when the job does not allow a
breakaway) or 1 for anything else. Windows only; standard library only.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys

PROCESS_CREATE_PROCESS = 0x0080
PROC_THREAD_ATTRIBUTE_PARENT_PROCESS = 0x00020000
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
ERROR_ACCESS_DENIED = 5


def spawn(parent_pid: int, flags: int, args: list[str]) -> int:
    from ctypes import wintypes

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.InitializeProcThreadAttributeList.argtypes = (ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                                      ctypes.POINTER(ctypes.c_size_t))
    k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    k32.UpdateProcThreadAttribute.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                              ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p)
    k32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    k32.DeleteProcThreadAttributeList.argtypes = (ctypes.c_void_p,)
    k32.DeleteProcThreadAttributeList.restype = None
    k32.CreateProcessW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                   wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.c_void_p, ctypes.c_void_p)
    k32.CreateProcessW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL

    def fail(what: str):
        code = ctypes.get_last_error()
        raise OSError(code, f"{what}: {ctypes.FormatError(code).strip()} (Windows error {code})")

    parent = k32.OpenProcess(PROCESS_CREATE_PROCESS, False, parent_pid)
    if not parent:
        fail(f"cannot open process {parent_pid}")
    parent_handle = wintypes.HANDLE(parent)
    size = ctypes.c_size_t(0)
    k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))        # asks for the size; "fails" by design
    if not size.value:
        fail("cannot size the attribute list")
    attrs = ctypes.create_string_buffer(size.value)
    if not k32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
        fail("cannot create the attribute list")
    try:
        if not k32.UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_PARENT_PROCESS,
                                             ctypes.byref(parent_handle), ctypes.sizeof(parent_handle), None, None):
            fail("cannot set the parent process")
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        si.lpAttributeList = ctypes.addressof(attrs)
        pi = PROCESS_INFORMATION()
        cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
        if not k32.CreateProcessW(None, cmd, None, None, False, flags | EXTENDED_STARTUPINFO_PRESENT, None, None,
                                  ctypes.byref(si), ctypes.byref(pi)):
            fail("cannot start the program")
        k32.CloseHandle(pi.hThread)
        k32.CloseHandle(pi.hProcess)
        return int(pi.dwProcessId)
    finally:
        k32.DeleteProcThreadAttributeList(attrs)
        k32.CloseHandle(parent_handle)


def main(argv: list[str]) -> int:
    if sys.platform != "win32":
        print("spawn_under: Windows only")
        return 1
    if len(argv) < 4:
        print("spawn_under: usage: spawn_under.py <parent pid> <creation flags> <program> [arguments ...]")
        return 1
    try:
        pid = spawn(int(argv[1]), int(argv[2], 0), argv[3:])
    except OSError as e:
        print(f"spawn_under: {e.strerror or e}")
        return 5 if e.errno == ERROR_ACCESS_DENIED else 1
    print(pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
