from __future__ import annotations

import ctypes
import os
import errno
import signal
import stat
import subprocess
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path


class Interrupted(Exception):
    def __init__(self, action="pause"):
        self.action = action
        super().__init__(action)


class ToolError(Exception):
    pass


class LockBusyError(ToolError):
    pass


def _windows_job():
    from ctypes import wintypes as w

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", w.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", w.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", w.DWORD),
            ("SchedulingClass", w.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basic),
            ("IoInfo", IO),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        if job:
            kernel.CloseHandle(job)
        raise ToolError("Windows could not create a protected worker process.")
    return kernel, job


class OwnedProcess:
    def __init__(self, args, *, cwd: Path, env: dict, log: Path, stdout: Path | None = None):
        log.parent.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.err = log.open("wb")
        self.out = stdout.open("wb") if stdout else self.err
        self.kernel = self.job = self.proc = None
        try:
            kwargs = {
                "cwd": cwd,
                "env": env,
                "stdin": subprocess.DEVNULL,
                "stdout": self.out,
                "stderr": self.err,
            }
            if os.name == "nt":
                self.kernel, self.job = _windows_job()
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | 0x4
            else:
                kwargs["start_new_session"] = True
            self.proc = subprocess.Popen([str(x) for x in args], **kwargs)
            if self.job:
                if not self.kernel.AssignProcessToJobObject(self.job, int(self.proc._handle)):
                    raise ToolError("Windows could not attach the worker to its cleanup boundary.")
                ntdll = ctypes.WinDLL("ntdll")
                ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
                if ntdll.NtResumeProcess(int(self.proc._handle)) != 0:
                    raise ToolError("Windows could not resume the worker.")
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self.proc:
                if self.job:
                    self.kernel.TerminateJobObject(self.job, 1)
                elif self.proc.poll() is None:
                    if os.name == "nt":
                        self.proc.kill()
                    else:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                if self.proc.poll() is None:
                    self.proc.kill()
                self.proc.wait(timeout=15)
        finally:
            if self.job:
                self.kernel.CloseHandle(self.job)
                self.job = None
            if self.out is not self.err:
                self.out.close()
            self.err.close()

    def wait(self, check=lambda: None, timeout=7200, byte_limit=None, watch: Path | None = None):
        start = time.monotonic()
        while self.proc.poll() is None:
            check()
            if time.monotonic() - start > timeout:
                raise ToolError("This stage exceeded its time limit. Completed work is saved.")
            if self.log.exists() and self.log.stat().st_size > 64 * 1024 * 1024:
                raise ToolError("The external tool produced excessive diagnostics.")
            if byte_limit and watch:
                size = 0
                for path in watch.rglob("*"):
                    try:
                        info = path.stat()
                    except FileNotFoundError:
                        # Downloaders rename temporary files while this directory is being sampled.
                        continue
                    if stat.S_ISREG(info.st_mode):
                        size += info.st_size
                if size > byte_limit:
                    raise ToolError("The download exceeded the configured size limit.")
            time.sleep(0.2)
        check()
        if self.proc.returncode:
            raise ToolError("An external tool failed. See the run's local diagnostic log.")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def run_tool(args, settings, folder: Path, name: str, check=lambda: None, **kwargs):
    folder.mkdir(parents=True, exist_ok=True)
    stdout = kwargs.pop("stdout", None)
    with OwnedProcess(
        args, cwd=folder, env=settings.environment(), log=folder / f"{name}.log", stdout=stdout
    ) as process:
        process.wait(check, **kwargs)


@contextmanager
def waiting_lock(path: Path, name: str, check):
    with ExitStack() as stack:
        while True:
            check()
            try:
                stack.enter_context(lock(path, name))
                break
            except LockBusyError:
                time.sleep(0.2)
        yield


@contextmanager
def lock(path: Path, name: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt" and name:
        from ctypes import wintypes as w

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
        kernel.CreateMutexW.restype = w.HANDLE
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.ReleaseMutex.argtypes = [w.HANDLE]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.CreateMutexW(None, False, name)
        if not handle:
            raise ToolError("Unable to acquire the GPU resource lock.")
        result = kernel.WaitForSingleObject(handle, 0)
        if result not in (0, 0x80):
            kernel.CloseHandle(handle)
            if result == 0x102:
                raise LockBusyError("Another clipper is using the GPU.")
            raise ToolError("Unable to acquire the GPU resource lock.")
        try:
            yield
        finally:
            kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        with path.open("a+b") as handle:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                if path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                    handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise LockBusyError("Another process holds this lock.") from exc
                    raise
            else:
                import fcntl

                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in (errno.EACCES, errno.EAGAIN):
                        raise LockBusyError("Another process holds this lock.") from exc
                    raise
            yield
