import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

logger = logging.getLogger("framesync")

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
WM_CLIPBOARDUPDATE = 0x031D
HWND_MESSAGE = ctypes.c_void_p(-3)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
user32.AddClipboardFormatListener.restype = wintypes.BOOL
user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
user32.RemoveClipboardFormatListener.restype = wintypes.BOOL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT
user32.GetMessageW.argtypes = [
    ctypes.POINTER(MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL


def _check(result: int, operation: str) -> None:
    if not result:
        raise ctypes.WinError(ctypes.get_last_error(), operation)


def get_text() -> str | None:
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        threading.Event().wait(0.02)
    else:
        raise ctypes.WinError(ctypes.get_last_error(), "OpenClipboard")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error(), "GlobalLock")
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_text(text: str) -> None:
    encoded = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(encoded)
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error(), "GlobalAlloc")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise ctypes.WinError(ctypes.get_last_error(), "GlobalLock")
    try:
        ctypes.memmove(pointer, encoded, size)
    finally:
        kernel32.GlobalUnlock(handle)

    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        threading.Event().wait(0.02)
    else:
        kernel32.GlobalFree(handle)
        raise ctypes.WinError(ctypes.get_last_error(), "OpenClipboard")
    transferred = False
    try:
        _check(user32.EmptyClipboard(), "EmptyClipboard")
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise ctypes.WinError(ctypes.get_last_error(), "SetClipboardData")
        transferred = True
    finally:
        user32.CloseClipboard()
        if not transferred:
            kernel32.GlobalFree(handle)


def watch(on_change: Callable[[str], None]) -> None:
    class_name = f"FrameSyncClipboard-{threading.get_ident()}"

    @WNDPROC
    def window_proc(hwnd, message, wparam, lparam):
        if message == WM_CLIPBOARDUPDATE:
            try:
                text = get_text()
                if text is not None:
                    on_change(text)
            except Exception:
                logger.exception("Could not read the Windows clipboard")
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    instance = kernel32.GetModuleHandleW(None)
    window_class = WNDCLASSW(
        0, window_proc, 0, 0, instance, None, None, None, None, class_name
    )
    atom = user32.RegisterClassW(ctypes.byref(window_class))
    _check(atom, "RegisterClassW")
    hwnd = user32.CreateWindowExW(
        0,
        class_name,
        class_name,
        0,
        0,
        0,
        0,
        0,
        HWND_MESSAGE,
        None,
        instance,
        None,
    )
    _check(hwnd, "CreateWindowExW")
    try:
        _check(user32.AddClipboardFormatListener(hwnd), "AddClipboardFormatListener")
        message = MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == -1:
                raise ctypes.WinError(ctypes.get_last_error(), "GetMessageW")
            if result == 0:
                return
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
    finally:
        user32.RemoveClipboardFormatListener(hwnd)
        user32.DestroyWindow(hwnd)
