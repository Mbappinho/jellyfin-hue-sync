"""Real-process lifetime-lock checks; no network or lamps are used."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from ambilight.state_lock import StateLock


class StateLockTests(unittest.TestCase):
    def test_lock_file_permissions_are_private_on_the_host_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock=StateLock(Path(tmp)/'state')
            try:
                if os.name!='nt':
                    self.assertEqual(lock.path.stat().st_mode&0o777,0o600)
                    self.assertEqual(lock.state_dir.stat().st_mode&0o777,0o700)
                    return
                import ctypes
                from ctypes import wintypes
                api=ctypes.WinDLL('advapi32',use_last_error=True)
                kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                descriptor=ctypes.c_void_p();text=wintypes.LPWSTR()
                api.GetNamedSecurityInfoW.argtypes=[wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD,
                    ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
                api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes=[ctypes.c_void_p,wintypes.DWORD,
                    wintypes.DWORD,ctypes.POINTER(wintypes.LPWSTR),ctypes.c_void_p]
                kernel.LocalFree.argtypes=[ctypes.c_void_p]
                self.assertEqual(api.GetNamedSecurityInfoW(str(lock.path),1,4,None,None,None,None,ctypes.byref(descriptor)),0)
                try:
                    self.assertTrue(api.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor,1,4,ctypes.byref(text),None))
                    sddl=text.value
                    self.assertTrue(sddl.startswith('D:P'),sddl)
                    self.assertEqual(set(__import__('re').findall(r';;;([^)]*)\)',sddl)),{'OW','SY'},sddl)
                finally:
                    if text: kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))
                    kernel.LocalFree(descriptor)
            finally: lock.close()

    def test_close_retains_state_lock_when_cache_stop_returns_with_live_worker(self):
        import threading
        from ambilight.engine import Engine
        from ambilight.service import build_engine
        resume=threading.Event()
        class Cache:
            def __init__(self):
                self._thread=threading.Thread(target=lambda:resume.wait(3),daemon=True)
                self._thread.start()
            def stop(self): pass  # Model a bounded join that returned before exit.
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state'
            engine=build_engine(state,Path(tmp)/'library',Path(tmp)/'settings',Path(tmp)/'transport')
            cache=Cache();engine.cache=cache
            contender=None
            try:
                with self.assertRaisesRegex(RuntimeError,'shutdown incomplete'):
                    engine.close(timeout=0.2)
                with self.assertRaisesRegex(RuntimeError,'already in use'):
                    contender=StateLock(state)
                resume.set();cache._thread.join(1)
                engine.close(timeout=0.2)
                contender=StateLock(state);contender.close();contender=None
            finally:
                resume.set();cache._thread.join(3)
                if contender is not None: contender.close()
                engine.close()

    def test_process_exit_releases_kernel_lock_without_removing_stale_file(self):
        import queue,threading
        holder='''
import sys
from pathlib import Path
from ambilight.service import build_engine
state=Path(sys.argv[1])
engine=build_engine(state,state.parent/'library',state.parent/'settings',state.parent/'transport')
print('LOCKED',flush=True)
sys.stdin.buffer.read(1)
engine.close()
'''
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state';state.mkdir()
            path=state/'.service.lock';path.write_bytes(b'NOT A PID LEASE')
            inode=path.stat().st_ino
            process=subprocess.Popen([sys.executable,'-c',holder,str(state)],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            ready=queue.Queue()
            reader=threading.Thread(target=lambda:ready.put(process.stdout.readline()),daemon=True);reader.start()
            contender=None
            try:
                self.assertIn(ready.get(timeout=5),(b'LOCKED\n',b'LOCKED\r\n'))
                with self.assertRaisesRegex(RuntimeError,'already in use'):
                    contender=StateLock(state)
                process.kill();process.wait(timeout=3)
                contender=StateLock(state)
                self.assertEqual(path.stat().st_ino,inode)
                self.assertFalse(os.get_inheritable(contender._fd))
                contender.close();contender=None
                self.assertEqual(path.read_bytes(),b'NOT A PID LEASE')
            finally:
                if process.poll() is None: process.kill();process.wait(timeout=3)
                for pipe in (process.stdin,process.stdout,process.stderr): pipe.close()
                reader.join(1)
                if contender is not None: contender.close()

    def test_subprocess_is_denied_until_bounded_close_finishes_cache_cleanup(self):
        import threading
        from unittest.mock import patch
        from ambilight.engine import Engine
        from ambilight.service import build_engine
        entered=threading.Event();resume=threading.Event();returned=threading.Event();errors=[]
        class Cache:
            def stop(self):
                entered.set()
                if not resume.wait(5): raise AssertionError('Fixture cleanup was not resumed')
        probe='''
import sys
from pathlib import Path
from ambilight.service import build_engine
state=Path(sys.argv[1])
try:
    engine=build_engine(state,state.parent/'library',state.parent/'settings',state.parent/'transport')
except RuntimeError as error:
    if 'already in use' not in str(error): raise
    print('DENIED BEFORE RECOVERY',flush=True)
    sys.exit(23)
engine.close()
print('ACQUIRED',flush=True)
'''
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state'
            engine=build_engine(state,Path(tmp)/'library',Path(tmp)/'settings',Path(tmp)/'transport')
            engine.cache=Cache()
            journal=state/'restore.json';journal.write_bytes(b'LIVE OFFLINE JOURNAL')
            config=(state/'config.json').read_bytes()
            def close():
                try: engine.close()
                except Exception as error: errors.append(error)
                finally: returned.set()
            closer=threading.Thread(target=close,daemon=True)
            try:
                with patch.object(Engine,'CLOSE_TIMEOUT',0.05,create=True):
                    closer.start();self.assertTrue(entered.wait(1))
                    self.assertTrue(returned.wait(0.5),'close waited indefinitely for blocked cleanup')
                self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],TimeoutError)
                denied=subprocess.run([sys.executable,'-c',probe,str(state)],capture_output=True,text=True,timeout=5)
                self.assertEqual(denied.returncode,23,denied.stdout+denied.stderr)
                self.assertIn('DENIED BEFORE RECOVERY',denied.stdout)
                self.assertEqual(journal.read_bytes(),b'LIVE OFFLINE JOURNAL')
                self.assertEqual((state/'config.json').read_bytes(),config)
                resume.set();engine.close();closer.join(1)
                acquired=subprocess.run([sys.executable,'-c',probe,str(state)],capture_output=True,text=True,timeout=5)
                self.assertEqual(acquired.returncode,0,acquired.stdout+acquired.stderr)
                self.assertIn('ACQUIRED',acquired.stdout)
                self.assertTrue((state/'.service.lock').exists())
            finally:
                resume.set();closer.join(5);engine.close()

    def test_state_directory_link_is_rejected_before_config_creation(self):
        from ambilight.service import build_engine
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'outside';target.mkdir()
            state=Path(tmp)/'state'
            if os.name=='nt':
                result=subprocess.run(['cmd','/c','mklink','/J',str(state),str(target)],capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
            else: state.symlink_to(target,target_is_directory=True)
            engine=None
            try:
                with self.assertRaisesRegex(RuntimeError,'symlink|reparse'):
                    engine=build_engine(state,Path(tmp)/'library',Path(tmp)/'settings',Path(tmp)/'transport')
                self.assertEqual(list(target.iterdir()),[])
            finally:
                if engine is not None: engine.close()

    def test_lock_config_and_journal_symlinks_are_rejected_without_changing_target(self):
        for name in ('.service.lock','config.json','restore.json'):
            with self.subTest(path=name),tempfile.TemporaryDirectory() as tmp:
                state=Path(tmp)/'state';state.mkdir()
                target=Path(tmp)/'outside';target.write_bytes(b'PRIVATE OUTSIDE FIXTURE')
                try: (state/name).symlink_to(target)
                except OSError as error: self.skipTest('Symlink creation unavailable: '+str(error))
                lock=None
                try:
                    with self.assertRaisesRegex(RuntimeError,'symlink|reparse'):
                        lock=StateLock(state)
                    self.assertEqual(target.read_bytes(),b'PRIVATE OUTSIDE FIXTURE')
                finally:
                    if lock is not None: lock.close()


if __name__=='__main__': unittest.main()
