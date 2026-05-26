import os
import subprocess
import sys
from typing import Optional


_SERVER_PROCESS: Optional[subprocess.Popen] = None

# Windows-only: keep a Job object handle open so child processes die when this
# app is force-closed (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE).
_JOB_HANDLE = None


def _windows_current_process_create_time_filetime():
    """Return the current process creation time as a FILETIME 64-bit integer."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        GetCurrentProcess = kernel32.GetCurrentProcess
        GetCurrentProcess.argtypes = ()
        GetCurrentProcess.restype = wintypes.HANDLE

        GetProcessTimes = kernel32.GetProcessTimes
        GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        )
        GetProcessTimes.restype = wintypes.BOOL

        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()

        ok = GetProcessTimes(
            GetCurrentProcess(),
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return None
        return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    except Exception:
        return None


def get_current_process_create_time_filetime():
    """Return current process creation time as a Windows FILETIME int (or None)."""
    return _windows_current_process_create_time_filetime()


def _windows_open_current_process_handle_for_watchdog():
    """Return an inheritable SYNCHRONIZE handle to the current process, or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        OpenProcess.restype = wintypes.HANDLE

        SYNCHRONIZE = 0x00100000
        handle = OpenProcess(SYNCHRONIZE, True, int(os.getpid()))
        if not handle:
            return None
        handle_int = int(handle)
        try:
            os.set_handle_inheritable(handle_int, True)
        except Exception:
            pass
        return handle_int
    except Exception:
        return None


def _windows_create_kill_on_close_job():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-setinformationjobobject
    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    CreateJobObjectW = kernel32.CreateJobObjectW
    CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    CreateJobObjectW.restype = wintypes.HANDLE

    SetInformationJobObject = kernel32.SetInformationJobObject
    SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    SetInformationJobObject.restype = wintypes.BOOL

    job = CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    ok = SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

    return job


def _windows_assign_process_to_job(job_handle, process_handle):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    AssignProcessToJobObject = kernel32.AssignProcessToJobObject
    AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    AssignProcessToJobObject.restype = wintypes.BOOL

    ok = AssignProcessToJobObject(job_handle, wintypes.HANDLE(int(process_handle)))
    if not ok:
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")


def _windows_open_process_for_job(pid: int):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    OpenProcess = kernel32.OpenProcess
    OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    OpenProcess.restype = wintypes.HANDLE

    # Minimal rights required for AssignProcessToJobObject.
    PROCESS_TERMINATE = 0x0001
    PROCESS_SET_QUOTA = 0x0100
    handle = OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, int(pid))
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    return handle


def _ensure_job_handle():
    global _JOB_HANDLE
    if os.name != "nt":
        return None
    if _JOB_HANDLE is not None:
        return _JOB_HANDLE
    try:
        _JOB_HANDLE = _windows_create_kill_on_close_job()
    except Exception:
        _JOB_HANDLE = None
    return _JOB_HANDLE


def start_managed_server(
    *,
    server_path: str,
    cwd: str,
    host: str,
    port: int,
    log_path: Optional[str] = None,
    python_exe: Optional[str] = None,
    creationflags: int = 0,
):
    """Start the local server as a managed child process.

    On Windows, the child is placed in a Job object configured to kill the
    server if this app is force-closed.
    """
    global _SERVER_PROCESS

    if _SERVER_PROCESS is not None and _SERVER_PROCESS.poll() is None:
        return _SERVER_PROCESS

    env = os.environ.copy()
    env["PRODSMART_HOST"] = str(host or "127.0.0.1")
    env["PRODSMART_PORT"] = str(int(port or 8000))
    # Allow the server process to detect when the GUI app has been force-closed
    # and self-terminate as a fallback (e.g., if Job object assignment fails).
    try:
        env["PRODSMART_PARENT_PID"] = str(os.getpid())
    except Exception:
        pass
    if os.name == "nt":
        try:
            ft = _windows_current_process_create_time_filetime()
            if ft is not None:
                env["PRODSMART_PARENT_CREATE_TIME"] = str(int(ft))
        except Exception:
            pass

    startupinfo = None
    parent_watch_handle = None
    if os.name == "nt":
        # Avoid PID reuse races: pass an inheritable HANDLE to the parent process
        # and let the server wait on it directly.
        try:
            parent_watch_handle = _windows_open_current_process_handle_for_watchdog()
            if parent_watch_handle is not None:
                env["PRODSMART_PARENT_HANDLE"] = str(int(parent_watch_handle))
                startupinfo = subprocess.STARTUPINFO(
                    lpAttributeList={"handle_list": [int(parent_watch_handle)]}
                )
        except Exception:
            parent_watch_handle = None
            startupinfo = None

    log_file = None
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    if log_path:
        try:
            log_file = open(log_path, "a", encoding="utf-8")
            stdout = log_file
            stderr = log_file
        except Exception:
            log_file = None

    try:
        proc = subprocess.Popen(
            [python_exe or sys.executable, server_path],
            cwd=cwd,
            env=env,
            stdout=stdout,
            stderr=stderr,
            startupinfo=startupinfo,
            close_fds=True if os.name == "nt" else True,
            creationflags=creationflags,
        )
        _SERVER_PROCESS = proc

        if os.name == "nt":
            job = _ensure_job_handle()
            if job is not None:
                try:
                    proc_handle = getattr(proc, "_handle", None)
                    opened = None
                    if proc_handle is None:
                        opened = _windows_open_process_for_job(proc.pid)
                        proc_handle = opened
                    _windows_assign_process_to_job(job, proc_handle)
                    if opened is not None:
                        try:
                            import ctypes

                            ctypes.windll.kernel32.CloseHandle(opened)
                        except Exception:
                            pass
                except Exception:
                    pass

        return proc
    finally:
        if parent_watch_handle is not None and os.name == "nt":
            try:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(int(parent_watch_handle))
            except Exception:
                pass
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass


def stop_managed_server(timeout_s: float = 1.5) -> bool:
    """Stop the managed server process (if this app started it)."""
    global _SERVER_PROCESS

    proc = _SERVER_PROCESS
    _SERVER_PROCESS = None
    if proc is None:
        return False
    try:
        if proc.poll() is not None:
            return True
        try:
            proc.terminate()
        except Exception:
            return False
        try:
            proc.wait(timeout=timeout_s)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return True
    except Exception:
        return False
