"""Read-only Windows process identity; no game injection or UI automation."""
import ctypes
from ctypes import wintypes
import os
import time

def process_started_ms(pid):
    if os.name != 'nt': return None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.GetProcessTimes.restype=wintypes.BOOL
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return None
    try:
        created,exited,kernel_time,user_time=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel_time),ctypes.byref(user_time)):return None
        ticks=(created.dwHighDateTime<<32)|created.dwLowDateTime
        return (ticks-116444736000000000)//10000
    finally:kernel.CloseHandle(handle)

def inspect_game_session():
    if os.name != 'nt':return None
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype=wintypes.BOOL
    kernel.Process32NextW.argtypes=kernel.Process32FirstW.argtypes
    kernel.Process32NextW.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot in (None,ctypes.c_void_p(-1).value):return None
    pids=[]
    try:
        entry=PROCESSENTRY32W();entry.dwSize=ctypes.sizeof(entry)
        ok=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while ok:
            if entry.szExeFile.casefold()=='screen stocks.exe':pids.append(int(entry.th32ProcessID))
            ok=kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    if len(pids)!=1:return None
    started=process_started_ms(pids[0])
    return {'pid':pids[0],'startedAtMs':started} if started is not None else None

class GameSessionProbe:
    def __init__(self):self.checked=0;self.value=None
    def __call__(self):
        if time.monotonic()-self.checked>=1:
            self.value=inspect_game_session();self.checked=time.monotonic()
        return self.value
