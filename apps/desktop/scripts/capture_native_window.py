"""Restore + position a window by pid, then capture it to PNG via PrintWindow.

Usage: python capture_native_window.py <pid> <out_png> [x y w h]

Evidence helper for the NUI-6/7 real-machine acceptance rounds. Physical-pixel
capture: declares per-monitor-v2 DPI awareness first, because a DPI-unaware
caller gets virtualized GetWindowRect values and PrintWindow then crops a
larger window. Channels are read as BGRX (GDI byte order) so vermillion never
turns blue.
"""

import ctypes
import ctypes.wintypes as wintypes
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:
    pass


def find_main_window(pid):
    result = []

    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindow(hwnd, 4) == 0:
            result.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return result[0] if result else 0


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
    pid = int(sys.argv[1])
    out = sys.argv[2]
    hwnd = find_main_window(pid)
    if not hwnd:
        print("NO_WINDOW")
        raise SystemExit(1)
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    time.sleep(0.6)
    if len(sys.argv) >= 7:
        x, y, w, h = (int(v) for v in sys.argv[3:7])
        user32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0004)  # SWP_NOZORDER
        time.sleep(0.8)
    ok, width, height = capture(hwnd, out)
    print(f"captured={bool(ok)} {width}x{height} -> {out}")
