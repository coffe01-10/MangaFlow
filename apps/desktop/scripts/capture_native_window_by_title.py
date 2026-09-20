"""Capture a visible top-level window of a pid by title substring, via PrintWindow.

Usage: python capture_native_window_by_title.py <pid> <title_substring> <out_png>

The NUI-6/7 helper capture_native_window.py only sees unowned top-level windows,
so modal dialogs created with Window.Owner (palette, confirm/input dialogs)
cannot be archived with it. This variant keeps the same physical-pixel path but
selects by title and never restores, moves or resizes the target window.
"""

import ctypes
import ctypes.wintypes as wintypes
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:
    pass


def find_windows(pid, needle):
    found = []

    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if needle.lower() in buffer.value.lower():
            found.append((hwnd, buffer.value))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def capture(hwnd, out_path):
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    hdc_window = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbm = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    gdi32.SelectObject(hdc_mem, hbm)
    ok = user32.PrintWindow(hwnd, hdc_mem, 0x00000002)  # PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    gdi32.GetDIBits(hdc_mem, hbm, 0, 0, None, ctypes.byref(bmi), 0)
    bmi.bmiHeader.biHeight = -height  # top-down
    buf = ctypes.create_string_buffer(bmi.bmiHeader.biSizeImage or width * height * 4)
    gdi32.GetDIBits(hdc_mem, hbm, 0, height, buf, ctypes.byref(bmi), 0)
    from PIL import Image
    image = Image.frombuffer("RGBX", (width, height), buf.raw, "raw", "BGRX", 0, 1)
    image.convert("RGB").save(out_path, "PNG")
    gdi32.DeleteObject(hbm)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)
    return ok, width, height


if __name__ == "__main__":
    pid, needle, out = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    matches = find_windows(pid, needle)
    if not matches:
        print(f"NO_WINDOW pid={pid} title~{needle!r}")
        raise SystemExit(1)
    hwnd, title = matches[0]
    ok, width, height = capture(hwnd, out)
    print(f"captured={bool(ok)} hwnd={hwnd} title={title!r} {width}x{height} matches={len(matches)} -> {out}")
