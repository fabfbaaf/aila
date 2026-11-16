"""用 Windows Job Object 管住后台命令的整棵进程树，不依赖父进程一直存活。

调用方先以 CREATE_SUSPENDED 启动进程，再 attach、resume，防止关联之前就产生逃逸的子进程。
关闭句柄也会结束全部后代；状态查询针对整个进程树，父 shell 先退出时仍能正确计数和停止。
只用标准库 ctypes，所有句柄和指针都按本机位宽声明，避免 64 位系统截断 HANDLE。
"""

import ctypes
import os
from ctypes import wintypes
from functools import lru_cache
from typing import Any

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
THREAD_SUSPEND_RESUME = 0x0002
TH32CS_SNAPTHREAD = 0x0004
ERROR_NO_MORE_FILES = 18
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


@lru_cache(maxsize=1)
def _api() -> Any:
    if os.name != "nt":
        raise OSError("Windows 进程树管理只能在 Windows 上使用。")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
        "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = args
        function.restype = result
    return api


def _error(action: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{action}（Windows 错误 {code}：{ctypes.FormatError(code).strip()}）")


class WindowsJob:
    """拥有一个不可继承的 Job 句柄；调用方结束读取后要 close，重复关闭无影响。"""

    def __init__(self) -> None:
        self._handle: int | None = None
        api = _api()
        handle = api.CreateJobObjectW(None, None)
        if not handle:
            raise _error("没法创建后台进程树")
        self._handle = handle
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                          ctypes.byref(limits), ctypes.sizeof(limits)):
            error = _error("没法设置后台进程树的清理方式")
            self.close()
            raise error

    def attach(self, pid: int) -> None:
        """加入尚未开始执行的根进程；它后来创建的子孙自动属于同一个 Job。"""
        if self._handle is None:
            raise OSError("后台进程树已经关闭，不能再加入进程。")
        api = _api()
        process = api.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not process:
            raise _error("没法打开刚启动的后台进程")
        try:
            if not api.AssignProcessToJobObject(self._handle, process):
                raise _error("没法把后台进程加入进程树")
        finally:
            api.CloseHandle(process)

    def resume(self, pid: int) -> None:
        """恢复 CREATE_SUSPENDED 的初始线程；目标还没运行，只会有这一条线程。"""
        if self._handle is None:
            raise OSError("后台进程树已经关闭，不能再启动进程。")
        api = _api()
        snapshot = api.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            raise _error("没法查找后台进程的初始线程")
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == pid:
                    thread = api.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                    if not thread:
                        raise _error("没法打开后台进程的初始线程")
                    try:
                        if api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise _error("没法恢复后台进程的初始线程")
                    finally:
                        api.CloseHandle(thread)
                    return
                entry.dwSize = ctypes.sizeof(entry)
                found = api.Thread32Next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
                raise _error("没法读取后台进程的线程列表")
            raise OSError("刚启动的后台进程已经没有初始线程。")
        finally:
            api.CloseHandle(snapshot)

    @property
    def running(self) -> bool:
        if self._handle is None:
            return False
        info = _Accounting()
        if not _api().QueryInformationJobObject(self._handle, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                                               ctypes.byref(info), ctypes.sizeof(info), None):
            raise _error("没法查询后台进程树的状态")
        return info.ActiveProcesses > 0

    def stop(self) -> None:
        """按 Job 句柄停止全部后代，不会误杀复用了旧 PID 的其他进程。"""
        if self._handle is not None and not _api().TerminateJobObject(self._handle, 1):
            raise _error("没法停掉后台进程树")

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None and not _api().CloseHandle(handle):
            raise _error("没法关闭后台进程树")
