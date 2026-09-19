"""Gracefully close only the WPF client (keep dev stack running)."""
import ctypes
import ctypes.wintypes as wintypes
import sys
import time

pid = int(sys.argv[1])
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")

result = []


def callback(hwnd, _):
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    if owner.value == pid and user32.IsWindowVisible(hwnd):
        result.append(hwnd)
    return True


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(WNDENUMPROC(callback), 0)
for hwnd in result:
    user32.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)  # WM_CLOSE
for _ in range(45):
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        print("closed")
        break
    kernel32.CloseHandle(handle)
    time.sleep(1)
else:
    print("still running after 45s")
