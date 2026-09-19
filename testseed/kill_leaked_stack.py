"""One-off cleanup of the leaked run-p1-first dev stack (verified-image tree kill).

The orchestrator's named-job reopen path is broken (job name is lost once the
starter exits) — this mirrors measure-native-startup.ps1's Stop-SampleTree
recipe: kill the ROOT handle-pinned, then kill each descendant only while its
live image still matches the pre-kill snapshot.
"""
import ctypes
import ctypes.wintypes as wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001


def descendants(root_pid):
    seen = {}
    frontier = [root_pid]
    while frontier:
        nxt = []
        for parent in frontier:
            snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)

            class PE(ctypes.Structure):
                _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                            ("th32ProcessID", wintypes.DWORD),
                            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                            ("th32ParentProcessID", wintypes.DWORD),
                            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                            ("szExeFile", ctypes.c_wchar * 260)]

            entry = PE()
            entry.dwSize = ctypes.sizeof(PE)
            if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    if entry.th32ParentProcessID == parent and entry.th32ProcessID not in seen:
                        seen[entry.th32ProcessID] = entry.szExeFile
                        nxt.append(entry.th32ProcessID)
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
            kernel32.CloseHandle(snapshot)
        frontier = nxt
    return seen


def kill_tree(root_pid, expect_image):
    name = None
    handle = kernel32.OpenProcess(_SYNCHRONIZE | _PROCESS_TERMINATE, False, root_pid)
    if not handle:
        print(f"root {root_pid} already gone")
        return
    kernel32.CloseHandle(handle)
    # image check
    h = kernel32.OpenProcess(0x0400 | 0x0010, False, root_pid)
    buf = ctypes.create_unicode_buffer(260)
    size = wintypes.DWORD(260)
    if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
        name = buf.value.rsplit("\\", 1)[-1]
    kernel32.CloseHandle(h)
    if name != expect_image:
        print(f"REFUSE: pid {root_pid} is {name!r}, expected {expect_image!r}")
        return
    kids = descendants(root_pid)
    k = kernel32.OpenProcess(_PROCESS_TERMINATE, False, root_pid)
    kernel32.TerminateProcess(k, 1)
    kernel32.CloseHandle(k)
    for pid, image in kids.items():
        h = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
        if not h:
            continue
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        live = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        live_name = buf.value.rsplit("\\", 1)[-1] if live else None
        kernel32.CloseHandle(h)
        if live_name == image and image in {"node.exe", "npm.cmd", "cmd.exe", "python.exe", "uvicorn.exe", "next.exe"}:
            k = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
            if k:
                kernel32.TerminateProcess(k, 1)
                kernel32.CloseHandle(k)
                print(f"killed descendant {image}:{pid}")


kill_tree(37932, "cmd.exe")
