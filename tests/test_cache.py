from pathlib import Path
import tempfile,unittest
from ambilight.cache import ClipCache

class CacheTests(unittest.TestCase):
    def test_persistent_cache_evicts_old_colors_to_byte_budget_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();source=library/'fixture.mkv';source.write_bytes(b'UNIT SOURCE')
            item={'Id':'fixture','Path':'/media/fixture.mkv'}
            cache=ClipCache(root/'cache',library)
            frames=[[[1,2,3],[4,5,6]]]*4
            cache.put(item,0,frames)
            cache.max_bytes=cache._path(item,0).stat().st_size*2
            unrelated=cache.cache_root/'unrelated.json';unrelated.write_text('KEEP THIS FILE')
            for start in (30,60,90,120): cache.put(item,start,frames)
            owned=list(cache.cache_root.glob(cache._key(item)+'-*.json'))
            self.assertLessEqual(sum(path.stat().st_size for path in owned),cache.max_bytes)
            self.assertIsNone(cache.get(item,0))
            self.assertEqual(cache.get(item,120),frames[0])
            self.assertEqual(source.read_bytes(),b'UNIT SOURCE')
            self.assertEqual(unrelated.read_text(),'KEEP THIS FILE')

    def test_request_pins_current_and_next_chunks_while_evicting_stale_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'UNIT TEST')
            item={'Id':'fixture','Path':'/media/fixture.mkv','RunTimeTicks':1500000000}
            cache=ClipCache(root/'cache',library)
            frames=[[[1,2,3],[4,5,6]]]*4
            for start in (0,30,60,90): cache.put(item,start,frames)
            cache.max_bytes=sum(cache._path(item,start).stat().st_size for start in (0,30))
            try:
                cache.request(item,1)
                self.assertEqual(cache.get(item,0),frames[0]);self.assertEqual(cache.get(item,30),frames[0])
                self.assertIsNone(cache.get(item,60));self.assertIsNone(cache.get(item,90))
                cache.put(item,120,frames)
                self.assertEqual(cache.get(item,0),frames[0]);self.assertEqual(cache.get(item,30),frames[0])
                self.assertIsNone(cache.get(item,120))
            finally: cache.stop()

    def test_cache_root_must_not_overlap_source_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir()
            source=library/('a'*64+'-0.json');source.write_bytes(b'SOURCE MEDIA MUST NOT BE DELETED')
            for cache_root in (library,library/'colors',root):
                with self.subTest(cache_root=cache_root),self.assertRaisesRegex(ValueError,'overlap'):
                    ClipCache(cache_root,library)
            self.assertEqual(source.read_bytes(),b'SOURCE MEDIA MUST NOT BE DELETED')

    def test_restart_enforces_persistent_chunk_count_budget(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'UNIT TEST')
            item={'Id':'fixture','Path':'/media/fixture.mkv'}
            cache=ClipCache(root/'cache',library)
            for start in (0,30,60,90): cache.put(item,start,[[[1,2,3],[4,5,6]]])
            with patch.object(ClipCache,'max_chunks',2,create=True):
                restarted=ClipCache(root/'cache',library)
                self.assertLessEqual(len(list(restarted.cache_root.glob('*.json'))),2)
                self.assertIsNone(restarted.get(item,0))
                self.assertEqual(restarted.get(item,90),[[1,2,3],[4,5,6]])

    def test_stop_unblocks_local_cli_even_when_docker_kill_is_unavailable(self):
        import subprocess,sys,threading,time
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'UNIT TEST')
            cache=ClipCache(root/'cache',library,image='sha256:'+'a'*64)
            item={'Id':'fixture','Path':'/media/fixture.mkv'}
            process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],stdout=subprocess.PIPE)
            spawned=threading.Event();done=threading.Event()
            def spawn(*args,**kwargs): spawned.set();return process
            def extract():
                try: cache._extract(item,0,lambda _:None)
                except Exception: pass
                finally: done.set()
            with patch('subprocess.Popen',side_effect=spawn),patch('subprocess.run',side_effect=OSError('Offline Docker fixture')):
                worker=threading.Thread(target=extract,daemon=True);cache._thread=worker;worker.start()
                try:
                    self.assertTrue(spawned.wait(1))
                    started=time.monotonic()
                    try: cache.stop()
                    except OSError: self.fail('Failed Docker kill prevented local CLI cleanup')
                    self.assertLess(time.monotonic()-started,.6)
                    self.assertTrue(done.is_set());self.assertIsNotNone(process.poll())
                finally:
                    process.kill();process.wait(timeout=2);worker.join(2)

    def test_cache_root_is_rechecked_before_writing_not_after_source_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'UNIT TEST')
            item={'Id':'fixture','Path':'/media/fixture.mkv'}
            cache=ClipCache(root/'cache',library)
            source=library/cache._path(item,0).name;source.write_bytes(b'SOURCE MEDIA')
            cache.cache_root=library
            with self.assertRaisesRegex(ValueError,'overlap'):
                cache.put(item,0,[[[1,2,3],[4,5,6]]])
            self.assertEqual(source.read_bytes(),b'SOURCE MEDIA')

    def test_extraction_does_not_spool_unbounded_unused_stderr_to_disk(self):
        import io,subprocess
        from unittest.mock import patch,MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'UNIT TEST')
            cache=ClipCache(root/'cache',library,image='sha256:'+'a'*64)
            process=MagicMock();process.stdout=io.BytesIO(b'\xff\0\0'*(96*54));process.wait.return_value=0
            with patch('subprocess.Popen',return_value=process) as spawn:
                cache._extract({'Id':'fixture','Path':'/media/fixture.mkv'},0,lambda _:None)
            self.assertEqual(spawn.call_args.kwargs['stderr'],subprocess.DEVNULL)

    def test_protected_chunks_cannot_bypass_disk_budget_with_unbounded_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'UNIT TEST')
            item={'Id':'fixture','Path':'/media/fixture.mkv'}
            cache=ClipCache(root/'cache',library)
            for frames in ([[[1,2,3],[4,5,6]]]*125,
                           [[[1,2,3],[4,5,'x'*1000000]]],
                           [[[1,2,300],[4,5,6]]],
                           [[[1,2,3]]]):
                with self.subTest(frame_count=len(frames)),self.assertRaisesRegex(ValueError,'color chunk'):
                    cache.put(item,0,frames)
                self.assertFalse(cache._path(item,0).exists())

    def test_failed_local_cli_start_clears_extraction_ownership(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'UNIT TEST')
            cache=ClipCache(root/'cache',library,image='sha256:'+'a'*64)
            with patch('subprocess.Popen',side_effect=OSError('Offline CLI fixture')):
                with self.assertRaises(OSError):
                    cache._extract({'Id':'fixture','Path':'/media/fixture.mkv'},0,lambda _:None)
            self.assertIsNone(cache._container);self.assertIsNone(cache._process)

    def test_watchdog_kills_local_cli_when_docker_kill_fails(self):
        import subprocess,sys,threading
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'UNIT TEST')
            cache=ClipCache(root/'cache',library,image='sha256:'+'a'*64)
            cache.extraction_timeout=.1
            item={'Id':'fixture','Path':'/media/fixture.mkv'}
            process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],stdout=subprocess.PIPE)
            errors=[];done=threading.Event()
            def extract():
                try: cache._extract(item,0,lambda _:None)
                except Exception as error: errors.append(error)
                finally: done.set()
            with patch('subprocess.Popen',return_value=process),patch('subprocess.run',side_effect=OSError('Offline Docker fixture')):
                worker=threading.Thread(target=extract,daemon=True);worker.start()
                try:
                    self.assertTrue(done.wait(.6),'Watchdog left stdout read blocked after failed Docker kill')
                    self.assertTrue(errors)
                    self.assertIsNotNone(process.poll())
                    self.assertIsNone(cache._container)
                finally:
                    process.kill();process.wait(timeout=2);worker.join(2)

    def test_custom_jellyfin_prefix_flows_through_cache_and_container_mapping(self):
        import io
        from unittest.mock import patch,MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'UNIT TEST')
            item={'Id':'fixture','Path':'/srv/videos/fixture.mkv','MediaStreams':[]}
            cache=ClipCache(root/'cache',library,mode='cpu',image='sha256:'+'a'*64,media_prefix='/srv/videos')
            cache.put(item,0,[[[1,2,3],[4,5,6]]])
            self.assertEqual(cache.get(item,0),[[1,2,3],[4,5,6]])
            process=MagicMock();process.stdout=io.BytesIO(b'\xff\0\0'*(96*54));process.wait.return_value=0
            with patch('subprocess.Popen',return_value=process) as spawn:
                cache._extract(item,0,lambda _:None)
            command=spawn.call_args.args[0]
            self.assertEqual(command[command.index('-i')+1],'/media/fixture.mkv')

    def test_persisted_colors_can_be_reused_without_decoding_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'EXPLICIT UNIT FIXTURE, NOT A VIDEO')
            item={'Id':'unit-fixture','Path':'/media/fixture.mkv','RunTimeTicks':600000000,'MediaStreams':[]}
            first=ClipCache(root/'cache',library)
            first.put(item,30,[[[255,0,0],[0,0,255]],[[0,255,0],[0,0,0]]])
            second=ClipCache(root/'cache',library)
            self.assertEqual(second.get(item,30.25),[[0,255,0],[0,0,0]])
            self.assertIsNone(second.get(item,31))

    def test_decode_requests_are_nonblocking_and_prepare_current_and_next_chunks(self):
        import threading,time
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); library=root/'library'; library.mkdir(); (library/'fixture.mkv').write_bytes(b'UNIT TEST ONLY')
            item={'Id':'fixture','Path':'/media/fixture.mkv','RunTimeTicks':900000000,'MediaStreams':[]}
            calls=[];done=threading.Event()
            def explicit_fixture_extractor(item,start,progress):
                calls.append(start)
                if start==30: done.set()
                return [[[128,0,0],[0,0,128]]]*120
            cache=ClipCache(root/'cache',library,extractor=explicit_fixture_extractor)
            try:
                started=time.monotonic();cache.request(item,1)
                self.assertLess(time.monotonic()-started,0.1)
                self.assertTrue(done.wait(2))
                for _ in range(20):
                    if cache.get(item,30) is not None: break
                    time.sleep(0.01)
                self.assertEqual(cache.get(item,1),[[128,0,0],[0,0,128]])
                self.assertEqual(cache.get(item,30),[[128,0,0],[0,0,128]])
                self.assertEqual(calls,[0,30])
            finally: cache.stop()

    def test_process_driver_decodes_actual_rgb_payload_and_releases_resources(self):
        import io
        from unittest.mock import patch as mock_patch, MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);library=root/'library';library.mkdir();(library/'fixture.mkv').write_bytes(b'EXPLICIT UNIT SOURCE')
            item={'Id':'fixture','Path':'/media/fixture.mkv','RunTimeTicks':600000000,'MediaStreams':[]}
            process=MagicMock();process.stdout=io.BytesIO(b'\xff\0\0'*(96*54*2));process.wait.return_value=0
            cache=ClipCache(root/'cache',library,image='sha256:'+'a'*64); progress=[]
            with mock_patch('subprocess.Popen',return_value=process) as spawn:
                frames=cache._extract(item,0,progress.append)
            self.assertEqual(frames,[[[255,0,0],[255,0,0]]]*2)
            self.assertEqual(progress,[1,2])
            command=spawn.call_args.args[0]
            self.assertIn('--network',command);self.assertIn('--read-only',command)
            self.assertTrue(process.stdout.closed)
            self.assertIsNone(cache._container)

if __name__=='__main__': unittest.main()
