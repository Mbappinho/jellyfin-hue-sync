"""Kernel-owned state-directory lifetime lock; never unlink a lock inode."""
import os
import stat
from pathlib import Path


def _reject_links(path):
    """Reject symlinks and Windows junction/reparse points, including parents."""
    path=Path(os.path.abspath(path))
    for component in (path,*path.parents):
        try: info=component.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise RuntimeError('State paths must not contain symlinks or reparse points.')


def _open_windows_lock(path):
    """Open without following reparse points or permitting lock-file deletion."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
        ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    # WRITE_DAC is needed to protect the DACL through this exact open handle.
    handle=kernel.CreateFileW(str(path),0xC0060000,3,None,4,0x00200000,None)
    if handle==ctypes.c_void_p(-1).value:
        raise RuntimeError('State lock is unavailable.')
    try: return msvcrt.open_osfhandle(handle,os.O_RDWR)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def _private_windows_lock(fd):
    """Protect the open lock with an owner/SYSTEM-only, non-inherited DACL."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    descriptor=ctypes.c_void_p();dacl=ctypes.c_void_p()
    present=wintypes.BOOL();defaulted=wintypes.BOOL()
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    api.GetSecurityDescriptorDacl.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wintypes.BOOL)]
    api.SetSecurityInfo.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,
        ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
    api.SetSecurityInfo.restype=wintypes.DWORD
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;FA;;;OW)(A;;FA;;;SY)',1,
            ctypes.byref(descriptor),None):
        raise RuntimeError('Private state lock permissions are unavailable.')
    try:
        if not api.GetSecurityDescriptorDacl(descriptor,ctypes.byref(present),ctypes.byref(dacl),ctypes.byref(defaulted)):
            raise RuntimeError('Private state lock permissions are unavailable.')
        if api.SetSecurityInfo(msvcrt.get_osfhandle(fd),1,0x80000004,None,None,dacl,None):
            raise RuntimeError('Private state lock permissions are unavailable.')
    finally:
        kernel.LocalFree(descriptor)


class StateLock:
    """Nonblocking exclusive lock, automatically released by process exit."""

    def __init__(self, state_dir):
        self.state_dir = Path(os.path.abspath(state_dir))
        _reject_links(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.state_dir / '.service.lock'
        for path in (self.path,self.state_dir/'config.json',self.state_dir/'restore.json'):
            _reject_links(path)
        self._fd = (_open_windows_lock(self.path) if os.name=='nt' else
                    os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os,'O_NOFOLLOW',0), 0o600))
        try:
            _reject_links(self.path)
            opened=os.fstat(self._fd);named=self.path.lstat()
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(named.st_dev,named.st_ino):
                raise RuntimeError('State lock must be a regular, unchanged file.')
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError('State directory is already in use.') from None
            if os.name=='nt': _private_windows_lock(self._fd)
            else: os.fchmod(self._fd,0o600)
            os.chmod(self.state_dir, 0o700)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
