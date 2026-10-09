import threading,unittest
from ambilight import service
class ServiceTests(unittest.TestCase):
    def test_runtime_contract_requires_portable_inputs_and_explicit_wildcard_hosts(self):
        from ambilight.__main__ import parse_args
        import contextlib,io
        required=['--library-root','/library','--api-key-file','/private/key','--image','sha256:'+'a'*64]
        with contextlib.redirect_stderr(io.StringIO()):
            for args in ([],required+['--bind','0.0.0.0'],required+['--bind','8.8.8.8']):
                with self.subTest(args=args),self.assertRaises(SystemExit):parse_args(args)
        args=parse_args(required+['--bind','0.0.0.0','--allowed-host','192.168.50.2','--allowed-host','hue-sync.local','--decoder','cpu','--media-prefix','/videos'])
        self.assertEqual(args.allowed_host,['192.168.50.2','hue-sync.local'])
        self.assertEqual(args.media_prefix,'/videos')
        self.assertEqual(args.decoder,'cpu')

    def test_module_help_lists_bind_state_and_readonly_media_options(self):
        import subprocess,sys
        result=subprocess.run([sys.executable,'-m','ambilight','--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        for flag in ('--bind','--state-dir','--library-root','--settings-file','--transport-dir'):
            self.assertIn(flag,result.stdout)

    def test_build_engine_creates_private_disabled_config_without_network_requests(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state'
            self.assertTrue(callable(getattr(service,'build_engine',None)),'Runtime construction missing')
            engine=service.build_engine(state,Path(tmp)/'library',Path(tmp)/'settings.json',Path(tmp)/'transport')
            self.assertFalse(engine.status()['paired']);self.assertFalse(engine.status()['config']['enabled'])
            self.assertTrue((state/'config.json').is_file())
            self.assertEqual(engine.client.base_url,'http://127.0.0.1:8096')
            self.assertEqual(engine.cache.library_root,Path(tmp)/'library')
            self.assertIn('fr.theo.hueserver.StreamMain',engine.java_command)
            engine.close()

    def test_second_builder_cannot_touch_live_config_or_recovery_journal(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state'
            first=service.build_engine(state,Path(tmp)/'library',Path(tmp)/'settings.json',Path(tmp)/'transport')
            second=None
            try:
                journal=state/'restore.json';journal.write_bytes(b'LIVE OFFLINE FIXTURE')
                config=(state/'config.json').read_bytes()
                with patch('ambilight.jellyfin.JellyfinClient',side_effect=AssertionError('Second instance reached construction')):
                    with self.assertRaisesRegex(RuntimeError,'already in use'):
                        second=service.build_engine(state,Path(tmp)/'library',Path(tmp)/'settings.json',Path(tmp)/'transport')
                self.assertEqual(journal.read_bytes(),b'LIVE OFFLINE FIXTURE')
                self.assertEqual((state/'config.json').read_bytes(),config)
            finally:
                if second is not None: second.close()
                first.close()
            replacement=service.build_engine(state,Path(tmp)/'library',Path(tmp)/'settings.json',Path(tmp)/'transport')
            replacement.close()

    def test_runtime_java_honors_java_home_without_personal_install_paths(self):
        import os,tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'jdk';binary=home/'bin'/('java.exe' if os.name=='nt' else 'java')
            binary.parent.mkdir(parents=True);binary.write_bytes(b'EXPLICIT TEST ONLY')
            with patch.dict(os.environ,{'JAVA_HOME':str(home)}):
                engine=service.build_engine(Path(tmp)/'state',Path(tmp)/'library',Path(tmp)/'settings',Path(tmp)/'dist')
            try:self.assertEqual(engine.java_command[0],str(binary))
            finally:engine.close()

    def test_builder_passes_file_decoder_prefix_and_caps_java_heap(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            engine=service.build_engine(Path(tmp)/'state',Path(tmp)/'library',None,Path(tmp)/'transport',
                image='sha256:'+'a'*64,api_key_file=Path(tmp)/'key',decoder='cpu',media_prefix='/videos')
            try:
                self.assertEqual(engine.client.api_key_file,Path(tmp)/'key')
                self.assertEqual(engine.cache.mode,'cpu')
                self.assertEqual(engine.cache.media_prefix,'/videos')
                self.assertIn('-Xmx128m',engine.java_command)
            finally:engine.close()

    def test_stop_during_poll_cancels_before_http_shutdown_and_skips_later_dispatch(self):
        stop=threading.Event();events=[]
        class Engine:
            def poll(self): events.append('poll');stop.set()
            def begin_shutdown(self): events.append('cancel')
            def refresh_areas(self): events.append('refresh')
            def tick(self,now): events.append('tick')
            def close(self): events.append('close')
        class Server:
            def __init__(self): self.done=threading.Event()
            def serve_forever(self,poll_interval=0.2): self.done.wait(2)
            def shutdown(self): events.append('shutdown');self.done.set()
            def server_close(self): events.append('server_close')
        service.run(Engine(),Server(),stop)
        self.assertEqual(events,['poll','cancel','shutdown','server_close','close'])

    def test_poll_worker_returning_after_join_timeout_never_dispatches_after_cleanup(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from ambilight.engine import Engine
        from tests.test_engine import FixtureBridge,FixtureCache,fixture_session,unit_config
        entered=threading.Event();resume=threading.Event();stop=threading.Event();errors=[];bridges=[]
        class Client:
            def sessions(self):
                entered.set()
                if not resume.wait(3): raise AssertionError('Fixture poll was not resumed')
                return [fixture_session()]
        class Cache(FixtureCache):
            stopped=False
            def stop(self): self.stopped=True
        class Server:
            def __init__(self): self.done=threading.Event()
            def serve_forever(self,poll_interval=0.2): self.done.wait(3)
            def shutdown(self): self.done.set()
            def server_close(self): pass
        def bridge(*args,**kwargs):
            bridges.append('bridge');return FixtureBridge(*args,**kwargs)
        original_join=threading.Thread.join
        def bounded_join(thread,timeout=None):
            return original_join(thread,0.02 if thread.name=='sync-loop' else timeout)
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps(unit_config()))
            cache=Cache();engine=Engine(cfg,Client(),cache,[],bridge)
            def run():
                try: service.run(engine,Server(),stop)
                except Exception as error: errors.append(error)
            runner=threading.Thread(target=run,name='fixture-service',daemon=True)
            worker=None
            try:
                with patch.object(threading.Thread,'join',bounded_join):
                    runner.start();self.assertTrue(entered.wait(1))
                    worker=next(thread for thread in threading.enumerate() if thread.name=='sync-loop')
                    stop.set();runner.join(0.5)
                    self.assertFalse(runner.is_alive());self.assertTrue(worker.is_alive())
                    self.assertTrue(cache.stopped);closed=engine.status()
                resume.set();worker.join(1)
                self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
                self.assertEqual(bridges,[]);self.assertEqual(cache.requests,[])
                self.assertEqual(engine.status(),closed)
            finally:
                resume.set();stop.set();original_join(runner,3)
                if worker is not None: original_join(worker,3)
                engine.close()

    def test_daemon_polls_ticks_and_closes_owned_resources_on_stop(self):
        stop=threading.Event()
        class Engine:
            polls=0;ticks=0;closed=False;refreshed=False
            def poll(self): self.polls+=1
            def tick(self,now): self.ticks+=1;stop.set()
            def refresh_areas(self): self.refreshed=True
            def begin_shutdown(self): pass
            def close(self): self.closed=True
        class Server:
            closed=False;shutdown_called=False
            def __init__(self): self.done=threading.Event()
            def serve_forever(self,poll_interval=0.2): self.done.wait(5)
            def shutdown(self): self.shutdown_called=True;self.done.set()
            def server_close(self): self.closed=True
        engine=Engine();server=Server();service.run(engine,server,stop)
        self.assertEqual(engine.polls,1);self.assertEqual(engine.ticks,1)
        self.assertTrue(engine.closed);self.assertTrue(engine.refreshed)
        self.assertTrue(server.closed);self.assertTrue(server.shutdown_called)
if __name__=='__main__': unittest.main()
