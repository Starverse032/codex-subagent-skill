from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys

def launch_flags(detached=False):
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {'start_new_session': True} if detached else {}

class OwnedProcess:
    """Own one new process tree. Never locate/kill processes by name or saved PID."""
    def __init__(self, argv, *, cwd: Path, stdout=None, stderr=None, env=None,
                 stdin=subprocess.DEVNULL, job_name=None):
        self.job = None
        self.job_name = job_name
        kwargs = launch_flags(True)
        if os.name == 'nt':
            # Assign before the worker executes, so it cannot spawn outside the job.
            kwargs['creationflags'] |= 0x00000004  # CREATE_SUSPENDED
        self.proc = subprocess.Popen(argv, cwd=str(cwd), stdin=stdin,
                                     stdout=stdout, stderr=stderr, env=env, **kwargs)
        if os.name == 'nt':
            try:
                self._attach_job()
            except BaseException:
                self.proc.kill()
                self.proc.wait()
                raise

    def _attach_job(self):
        import ctypes as ct
        from ctypes import wintypes as wt
        kernel = ct.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ct.c_void_p, wt.LPCWSTR]
        kernel.CreateJobObjectW.restype = wt.HANDLE
        kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        kernel.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
        class BASIC(ct.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ct.c_int64), ('PerJobUserTimeLimit', ct.c_int64),
                        ('LimitFlags', wt.DWORD), ('MinimumWorkingSetSize', ct.c_size_t),
                        ('MaximumWorkingSetSize', ct.c_size_t), ('ActiveProcessLimit', wt.DWORD),
                        ('Affinity', ct.c_size_t), ('PriorityClass', wt.DWORD), ('SchedulingClass', wt.DWORD)]
        class IO(ct.Structure):
            _fields_ = [(n, ct.c_uint64) for n in ('ReadOperationCount', 'WriteOperationCount',
                       'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class EXT(ct.Structure):
            _fields_ = [('BasicLimitInformation', BASIC), ('IoInfo', IO),
                        ('ProcessMemoryLimit', ct.c_size_t), ('JobMemoryLimit', ct.c_size_t),
                        ('PeakProcessMemoryUsed', ct.c_size_t), ('PeakJobMemoryUsed', ct.c_size_t)]
        ct.set_last_error(0)
        job = kernel.CreateJobObjectW(None, self.job_name)
        if not job:
            raise ct.WinError(ct.get_last_error())
        if self.job_name and ct.get_last_error() == 183:
            kernel.CloseHandle(job)
            raise RuntimeError('this run already has an active owned job')
        info = EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        self.kernel, self.job = kernel, job
        try:
            if not kernel.SetInformationJobObject(job, 9, ct.byref(info), ct.sizeof(info)):
                raise ct.WinError(ct.get_last_error())
            if not kernel.AssignProcessToJobObject(job, int(self.proc._handle)):
                raise ct.WinError(ct.get_last_error())
            # Popen closes the initial thread handle; resume the suspended process.
            nt = ct.WinDLL('ntdll')
            nt.NtResumeProcess.argtypes = [wt.HANDLE]
            nt.NtResumeProcess.restype = ct.c_long
            if nt.NtResumeProcess(int(self.proc._handle)) != 0:
                raise RuntimeError('could not resume supervised worker')
        except BaseException:
            kernel.CloseHandle(job)
            self.job = None
            raise

    def stop(self):
        if self.job:
            self.kernel.TerminateJobObject(self.job, 130)
        elif os.name != 'nt':
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def close(self):
        # Also removes children left running after their parent returned.
        self.stop()
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None

    def identity(self):
        return {'pid': self.proc.pid, 'birth': process_birth(self.proc.pid),
                'job_name': self.job_name, 'group_id': self.proc.pid, 'platform': os.name}

def process_birth(pid):
    """Read process identity without signaling or terminating it."""
    if os.name == 'nt':
        import ctypes as ct
        from ctypes import wintypes as wt
        kernel=ct.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD]
        kernel.OpenProcess.restype=wt.HANDLE
        kernel.GetProcessTimes.argtypes=[wt.HANDLE]+[ct.POINTER(wt.FILETIME)]*4
        kernel.GetExitCodeProcess.argtypes=[wt.HANDLE,ct.POINTER(wt.DWORD)]
        kernel.CloseHandle.argtypes=[wt.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:
            error=ct.get_last_error()
            if error in {87,1168}: return None
            raise ct.WinError(error)
        try:
            exit_code=wt.DWORD()
            if not kernel.GetExitCodeProcess(handle,ct.byref(exit_code)):
                raise ct.WinError(ct.get_last_error())
            if exit_code.value!=259: return None  # STILL_ACTIVE
            times=[wt.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*[ct.byref(t) for t in times]):
                raise ct.WinError(ct.get_last_error())
            return str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    stat=Path(f'/proc/{pid}/stat')
    if Path('/proc').is_dir():
        try:
            fields=stat.read_text().rsplit(')',1)[1].split()
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            return f'{boot}:{fields[19]}'
        except FileNotFoundError:
            return None
    result=subprocess.run(['ps','-p',str(pid),'-o','lstart='],capture_output=True,text=True,timeout=5)
    return result.stdout.strip() or None

def owned_tree_stopped(identity):
    """Conservatively verify a lost worker's tree is gone; never kill a saved PID."""
    if not isinstance(identity,dict) or identity.get('platform') != os.name:
        return False
    if os.name != 'nt':
        group=identity.get('group_id')
        if not isinstance(group,int) or group<=0: return False
        try:
            os.killpg(group,0)
            return False
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
    if not identity.get('job_name') or not identity.get('birth'):
        return False
    import ctypes as ct
    from ctypes import wintypes as wt
    kernel=ct.WinDLL('kernel32',use_last_error=True)
    kernel.OpenJobObjectW.argtypes=[wt.DWORD,wt.BOOL,wt.LPCWSTR]
    kernel.OpenJobObjectW.restype=wt.HANDLE
    kernel.QueryInformationJobObject.argtypes=[wt.HANDLE,ct.c_int,ct.c_void_p,wt.DWORD,ct.c_void_p]
    kernel.CloseHandle.argtypes=[wt.HANDLE]
    handle=kernel.OpenJobObjectW(0x0004,False,identity['job_name'])  # JOB_OBJECT_QUERY
    if handle:
        class ACCOUNTING(ct.Structure):
            _fields_=[('times',ct.c_int64*4),('faults',wt.DWORD),('total',wt.DWORD),
                      ('active',wt.DWORD),('terminated',wt.DWORD)]
        data=ACCOUNTING()
        try:
            if not kernel.QueryInformationJobObject(handle,1,ct.byref(data),ct.sizeof(data),None):
                return False
            if data.active: return False
        finally:
            kernel.CloseHandle(handle)
    elif ct.get_last_error()!=2:
        return False
    try:
        return process_birth(identity['pid']) != identity['birth']
    except (OSError,KeyError):
        return False


def console_cli(argv):
    """Run a CLI in this process's private Windows console and relay its exit code."""
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    # A detached supervisor may have inherited Ctrl+C suppression. Clear the
    # inheritable flag; ignore Ctrl+C in this relay alone via a Python handler.
    kernel.SetConsoleCtrlHandler(None, False)
    signal.signal(signal.SIGINT, lambda *_: None)
    with subprocess.Popen(argv, stdin=sys.stdin.buffer, stdout=sys.stdout.buffer,
                          stderr=sys.stderr.buffer) as child:
        return child.wait()


if __name__ == '__main__' and os.name == 'nt' and sys.argv[1:2] == ['_console_cli']:
    raise SystemExit(console_cli(sys.argv[2:]))
