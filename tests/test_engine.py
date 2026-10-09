"""Explicit offline engine fixtures; these are not live Hue/Jellyfin results."""
import json,tempfile,unittest
from pathlib import Path
from ambilight.engine import Engine

USER='fixture_user_0123456789';KEY='0123456789abcdef0123456789abcdef'
def unit_config():
    return {'enabled':False,'device_id':'unit-device','area_id':'1','brightness':0.7,'smoothing':1.0,'offset_ms':0,'bridge_ip':'10.0.0.2','hue':{'ip':'10.0.0.2','username':USER,'clientkey':KEY}}
class FixtureBridge:
    def __init__(self,ip,username=None,clientkey=None,**kwargs):
        self.username=username;self.active=False;self.sent=[];self.releases=0;self.connections=[]
    def recover(self): return False
    def areas(self): return [{'id':str(n),'name':'Unit area '+str(n),'ids':[1,2] if n==1 else [3],'xs':[-1.0,1.0] if n==1 else [0.0],'active':self.active and self.connections[-1]==str(n),'owner':self.username if self.active and self.connections[-1]==str(n) else None} for n in (1,2)]
    def connect(self,area): self.active=True;self.connections.append(area)
    def send(self,data): self.sent.append(data)
    def release(self): self.active=False;self.releases+=1

class FixtureCache:
    status={'phase':'ready','frames':120,'mode':'unit','error':None}
    def __init__(self): self.requests=[]
    def request(self,item,position): self.requests.append((item['Id'],position))
    def get(self,item,position): return [(100,0,0),(0,0,100)]
    def stop(self): pass

def fixture_session(paused=False):
    return {'DeviceId':'unit-device','DeviceName':'Unit TV','Client':'Jellyfin Android TV','LastPlaybackCheckIn':'2026-10-09T01:00:00Z','NowPlayingItem':{'Id':'unit-item','Name':'Unit video','Path':'/media/unit.mkv','RunTimeTicks':1_000_000_000},'PlayState':{'PositionTicks':100_000_000,'IsPaused':paused}}
def fixture_wall_time():
    from datetime import datetime
    return datetime.fromisoformat('2026-10-09T01:00:00+00:00').timestamp()

class EngineTests(unittest.TestCase):
    def test_configuration_errors_are_clear_english_and_leave_disk_unchanged(self):
        cases = [
            ({'hue': {}}, 'Settings are not allowed.'),
            ({'enabled': 1}, 'Invalid enabled setting.'),
            ({'device_id': None}, 'Invalid device ID.'),
            ({'area_id': '01'}, 'Invalid Entertainment area.'),
            ({'brightness': -1}, 'Invalid color setting.'),
            ({'smoothing': 0}, 'Invalid color setting.'),
            ({'offset_ms': 5001}, 'Invalid offset.'),
            ({'enabled': True}, 'Pair the bridge, then choose an area and a device.'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            engine = Engine(cfg, None, FixtureCache(), [], FixtureBridge)
            for changes, message in cases:
                with self.subTest(changes=changes):
                    with self.assertRaises(ValueError) as raised:
                        engine.configure(changes)
                    self.assertEqual(str(raised.exception), message)
                    self.assertFalse(cfg.exists())
            with self.assertRaises(ValueError) as raised:
                engine.test_lights()
            self.assertEqual(str(raised.exception), 'Pair the bridge and choose an area before testing.')

    def test_session_device_fallback_and_poll_diagnostic_are_english(self):
        class OfflineClient:
            def sessions(self):
                raise RuntimeError('fixture-private-diagnostic')
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp) / 'config.json', OfflineClient(), FixtureCache(), [], FixtureBridge)
            engine.accept_sessions([{'DeviceId': 'Exact-ID_Aa'}], 0, fixture_wall_time())
            with self.subTest(copy='fallback'):
                self.assertEqual(engine.status()['devices'][0]['name'], 'Device')
                self.assertEqual(engine.status()['devices'][0]['id'], 'Exact-ID_Aa')
            engine.poll(0, fixture_wall_time())
            self.assertEqual(engine.status()['error'], 'Jellyfin is unavailable.')
            self.assertNotIn('fixture-private-diagnostic', json.dumps(engine.status()))
            engine.client.sessions = lambda: []
            engine.poll(1, fixture_wall_time())
            self.assertIsNone(engine.status()['error'])

    def test_changed_entertainment_owner_has_an_english_diagnostic(self):
        class ForeignOwnerBridge(FixtureBridge):
            def areas(self):
                return [{**area, 'owner': 'fixture-other-owner'} for area in super().areas()]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            cfg.write_text(json.dumps(unit_config()))
            engine = Engine(cfg, None, FixtureCache(), [], ForeignOwnerBridge)
            with self.assertRaises(RuntimeError) as raised:
                engine._ensure_stream(unit_config())
            self.assertEqual(str(raised.exception), 'Entertainment area ownership changed.')
            self.assertNotIn('fixture-other-owner', str(raised.exception))
            self.assertEqual(engine._bridge.sent, [])

    def test_duplicate_names_never_override_the_exact_selected_device_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            config = unit_config()
            config['device_id'] = 'old-ID_Aa'
            cfg.write_text(json.dumps(config))
            engine = Engine(cfg, None, FixtureCache(), [], FixtureBridge)
            active = fixture_session()
            active.update(DeviceId='new-ID_aA', DeviceName='Bedroom TV')
            idle = {'DeviceId': 'old-ID_Aa', 'DeviceName': 'Bedroom TV',
                    'Client': active['Client'], 'LastActivityDate': active['LastPlaybackCheckIn']}
            engine.accept_sessions([idle, active], 0, fixture_wall_time())
            self.assertEqual([item['id'] for item in engine.status()['devices']], ['old-ID_Aa', 'new-ID_aA'])
            self.assertEqual([item['active'] for item in engine.status()['devices']], [False, True])
            self.assertIsNone(engine.status()['playback']['title'])
            engine.configure({'device_id': 'new-ID_aA'})
            engine.accept_sessions([idle, active], 1, fixture_wall_time())
            self.assertEqual(engine.status()['playback']['title'], 'Unit video')
            for missing in ['new-ID_AA', 'Bedroom TV', 'missing-ID']:
                engine.configure({'device_id': missing})
                engine.accept_sessions([idle, active], 2, fixture_wall_time())
                self.assertIsNone(engine.status()['playback']['title'])
                self.assertEqual(engine.status()['config']['device_id'], missing)
            self.assertEqual(json.loads(cfg.read_text())['device_id'], 'missing-ID')

    def test_device_change_never_sends_colors_from_previous_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            engine=Engine(cfg,None,FixtureCache(),[],FixtureBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            engine.configure({'device_id':'unit-other-device'});engine.tick(1)
            self.assertFalse(engine.status()['streaming']);self.assertEqual(engine.status()['phase'],'waiting')
            self.assertIsNone(engine.status()['playback']['position'])
            self.assertEqual(engine.status()['frames_sent'],1)


    def test_stop_during_connection_prevents_even_first_stale_packet(self):
        holder={}
        class StopDuringConnect(FixtureBridge):
            def connect(self,area):
                super().connect(area);holder['engine'].configure({'enabled':False})
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            engine=Engine(cfg,None,FixtureCache(),[],StopDuringConnect);holder['engine']=engine
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            self.assertEqual(engine.status()['frames_sent'],0)
            self.assertEqual(engine._bridge.sent,[]);self.assertEqual(engine._bridge.releases,1)
            self.assertFalse(engine.status()['streaming'])


    def test_blocked_poll_returning_after_close_cannot_prepare_or_mutate_playback(self):
        import threading
        entered=threading.Event();resume=threading.Event()
        class Client:
            def sessions(self):
                entered.set()
                if not resume.wait(3): raise AssertionError('Fixture poll was not resumed')
                return [fixture_session()]
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            cache=FixtureCache();engine=Engine(cfg,Client(),cache,[],FixtureBridge)
            worker=threading.Thread(target=lambda:engine.poll(0,fixture_wall_time()),daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                engine.close()
                closed=engine.status()
                resume.set();worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(cache.requests,[])
                self.assertEqual(engine.status(),closed)
                engine.tick(1)
                self.assertIsNone(engine._bridge)
            finally:
                resume.set();worker.join(3);engine.close()

    def test_closed_engine_rejects_admin_mutations_without_touching_disk_or_hue(self):
        calls=[]
        class PairBridge(FixtureBridge):
            def pair(self): return unit_config()['hue']
        def bridge(*args,**kwargs):
            calls.append((args,kwargs));return PairBridge(*args,**kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps(unit_config()))
            engine=Engine(cfg,None,FixtureCache(),[],bridge)
            engine.close();original=cfg.read_bytes();closed=engine.status()
            for mutate in (lambda:engine.configure({'brightness':0.2}),lambda:engine.pair('10.0.0.2'),engine.test_lights):
                with self.subTest(operation=mutate),self.assertRaisesRegex(RuntimeError,'shutting down'):
                    mutate()
            self.assertEqual(engine.refresh_areas(),[])
            engine.accept_sessions([fixture_session()],1,fixture_wall_time());engine.tick(1)
            self.assertEqual(calls,[]);self.assertEqual(cfg.read_bytes(),original)
            self.assertEqual(engine.status(),closed)

    def test_shutdown_during_blocked_handshake_prevents_first_packet_and_area_query(self):
        import threading
        entered=threading.Event();resume=threading.Event();queries=[]
        class BlockedBridge(FixtureBridge):
            def connect(self,area):
                entered.set()
                if not resume.wait(3): raise AssertionError('Fixture handshake was not resumed')
                super().connect(area)
            def areas(self): queries.append('areas');return super().areas()
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            engine=Engine(cfg,None,FixtureCache(),[],BlockedBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time())
            worker=threading.Thread(target=lambda:engine.tick(0),daemon=True);worker.start()
            try:
                self.assertTrue(entered.wait(1))
                with self.assertRaisesRegex(TimeoutError,'remains locked'): engine.close(timeout=0.02)
                resume.set();worker.join(1);engine.close(timeout=1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(engine._bridge.sent,[])
                self.assertEqual(queries,[])
                self.assertEqual(engine._bridge.releases,1)
                self.assertEqual(engine.status()['frames_sent'],0)
            finally:
                resume.set();worker.join(3);engine.close()

    def test_blocked_pairing_returning_after_shutdown_cannot_query_or_save_credentials(self):
        import threading
        entered=threading.Event();resume=threading.Event();queries=[];errors=[]
        class Bridge(FixtureBridge):
            def pair(self):
                entered.set()
                if not resume.wait(3): raise AssertionError('Fixture pairing was not resumed')
                return unit_config()['hue']
            def areas(self): queries.append('areas');return super().areas()
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps(unit_config()))
            engine=Engine(cfg,None,FixtureCache(),[],Bridge);original=cfg.read_bytes()
            def pair():
                try: engine.pair('10.0.0.2')
                except Exception as error: errors.append(error)
            worker=threading.Thread(target=pair,daemon=True);worker.start()
            try:
                self.assertTrue(entered.wait(1));engine.begin_shutdown();resume.set();worker.join(1)
                self.assertFalse(worker.is_alive());self.assertEqual(queries,[])
                self.assertEqual(cfg.read_bytes(),original)
                self.assertEqual(len(errors),1);self.assertEqual(str(errors[0]),'Engine is shutting down.')
            finally:
                resume.set();worker.join(3);engine.close()

    def test_blocked_cache_read_returning_after_close_cannot_change_phase(self):
        import threading
        entered=threading.Event();resume=threading.Event()
        class Cache(FixtureCache):
            def get(self,item,position):
                entered.set()
                if not resume.wait(3): raise AssertionError('Fixture cache read was not resumed')
                return None
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            engine=Engine(cfg,None,Cache(),[],FixtureBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time())
            worker=threading.Thread(target=lambda:engine.tick(0),daemon=True);worker.start()
            try:
                self.assertTrue(entered.wait(1));engine.close();closed=engine.status()
                resume.set();worker.join(1);self.assertFalse(worker.is_alive())
                self.assertEqual(engine.status(),closed);self.assertIsNone(engine._bridge)
            finally:
                resume.set();worker.join(3);engine.close()

    def test_shutdown_during_area_refresh_discards_blocked_recovery_or_response(self):
        import threading
        for stage in ('recover','areas'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as tmp:
                entered=threading.Event();resume=threading.Event();queries=[];results=[]
                def block():
                    entered.set()
                    if not resume.wait(3): raise AssertionError('Fixture refresh was not resumed')
                class Bridge(FixtureBridge):
                    def recover(self):
                        if stage=='recover': block()
                    def areas(self):
                        queries.append('areas')
                        if stage=='areas': block()
                        return super().areas()
                cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps(unit_config()))
                engine=Engine(cfg,None,FixtureCache(),[],Bridge)
                worker=threading.Thread(target=lambda:results.append(engine.refresh_areas()),daemon=True);worker.start()
                try:
                    self.assertTrue(entered.wait(1));engine.begin_shutdown();resume.set();worker.join(1)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(results,[[]]);self.assertEqual(engine.status()['areas'],[])
                    self.assertEqual(queries,[] if stage=='recover' else ['areas'])
                finally:
                    resume.set();worker.join(3);engine.close()

    def test_failed_close_caller_cannot_report_success_during_concurrent_retry(self):
        import threading
        observed=threading.Event();first_resume=threading.Event();retry_entered=threading.Event();retry_resume=threading.Event()
        results=[]
        class Cache(FixtureCache):
            attempts=0
            def stop(self):
                self.attempts+=1
                if self.attempts==1: raise RuntimeError('OFFLINE FIXTURE cleanup failure')
                retry_entered.set()
                if not retry_resume.wait(3): raise AssertionError('Fixture cleanup retry was not resumed')
        class Completion(threading.Event):
            def wait(self,timeout=None):
                result=super().wait(timeout)
                if threading.current_thread().name=='first-close' and result:
                    observed.set()
                    if not first_resume.wait(3): raise AssertionError('Fixture first close was not resumed')
                return result
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine(Path(tmp)/'config.json',None,Cache(),[],FixtureBridge)
            engine._close_done=Completion()
            def first():
                try: engine.close(timeout=1);results.append('success')
                except RuntimeError: results.append('incomplete')
            caller=threading.Thread(target=first,name='first-close',daemon=True);caller.start()
            try:
                self.assertTrue(observed.wait(1))
                with self.assertRaises(TimeoutError): engine.close(timeout=0.05)
                self.assertTrue(retry_entered.is_set())
                first_resume.set();caller.join(1)
                self.assertEqual(results,['incomplete'])
            finally:
                first_resume.set();retry_resume.set();caller.join(3);engine.close()

    def test_shutdown_during_bridge_construction_prevents_recovery_and_connect(self):
        import threading
        entered=threading.Event();resume=threading.Event();recoveries=[]
        class Bridge(FixtureBridge):
            def recover(self): recoveries.append('recover')
        def factory(*args,**kwargs):
            entered.set()
            if not resume.wait(3): raise AssertionError('Fixture construction was not resumed')
            return Bridge(*args,**kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            engine=Engine(cfg,None,FixtureCache(),[],factory)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time())
            worker=threading.Thread(target=lambda:engine.tick(0),daemon=True);worker.start()
            try:
                self.assertTrue(entered.wait(1));engine.begin_shutdown();resume.set();worker.join(1)
                self.assertFalse(worker.is_alive());self.assertEqual(recoveries,[])
                self.assertEqual(engine._bridge.connections,[]);self.assertEqual(engine._bridge.sent,[])
            finally:
                resume.set();worker.join(3);engine.close()

    def test_close_restores_owned_lights_and_stops_extraction_without_erasing_enable_preference(self):
        class Cache(FixtureCache):
            stopped=False
            def stop(self): self.stopped=True
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            cache=Cache();engine=Engine(cfg,None,cache,[],FixtureBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            self.assertTrue(callable(getattr(engine,'close',None)),'Graceful shutdown missing')
            engine.close();self.assertTrue(cache.stopped);self.assertEqual(engine._bridge.releases,1)
            self.assertTrue(json.loads(cfg.read_text())['enabled']);self.assertFalse(engine.status()['streaming'])


    def test_poll_reads_jellyfin_and_prepares_only_target_playback(self):
        class Client:
            def __init__(self): self.calls=0
            def sessions(self): self.calls+=1;return [fixture_session()]
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            client=Client();cache=FixtureCache();engine=Engine(cfg,client,cache,[],FixtureBridge)
            self.assertTrue(callable(getattr(engine,'poll',None)),'Read-only polling integration missing')
            engine.poll(0,fixture_wall_time());engine.tick(0)
            self.assertEqual(client.calls,1);self.assertTrue(engine.status()['streaming'])
            self.assertEqual(engine.status()['devices'][0]['id'],'unit-device')


    def test_start_requires_pairing_area_and_exact_device_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';engine=Engine(cfg,None,FixtureCache(),[],FixtureBridge)
            with self.assertRaises(ValueError): engine.configure({'enabled':True})
            self.assertFalse(engine.status()['config']['enabled']);self.assertFalse(cfg.exists())


    def test_static_light_test_works_without_movie_and_restores_after_four_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps(unit_config()))
            engine=Engine(cfg,None,FixtureCache(),[],FixtureBridge)
            self.assertTrue(callable(getattr(engine,'test_lights',None)),'Static lighting test missing')
            engine.test_lights();engine.tick(0)
            self.assertEqual(engine.status()['phase'],'testing');self.assertTrue(engine.status()['streaming'])
            self.assertFalse(engine.status()['config']['enabled'])
            bridge=engine._bridge;engine.tick(4.01)
            self.assertFalse(engine.status()['streaming']);self.assertEqual(bridge.releases,1)
            self.assertEqual(engine.status()['phase'],'disabled')


    def test_saved_pairing_refreshes_available_areas_without_activating_any_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps(unit_config()))
            engine=Engine(cfg,None,FixtureCache(),[],FixtureBridge)
            self.assertTrue(callable(getattr(engine,'refresh_areas',None)),'Startup area discovery missing')
            areas=engine.refresh_areas()
            self.assertEqual([a['id'] for a in areas],['1','2'])
            self.assertTrue(all(not a['active'] for a in areas));self.assertNotIn(USER,json.dumps(areas))
            self.assertEqual(areas,engine.status()['areas'])
            self.assertFalse(engine.status()['streaming'])


    def test_pair_saves_credentials_privately_and_exposes_only_selectable_areas(self):
        class PairBridge(FixtureBridge):
            def pair(self):
                self.username=USER
                return {'ip':'10.0.0.2','username':USER,'clientkey':KEY}
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';engine=Engine(cfg,None,FixtureCache(),[],PairBridge)
            self.assertTrue(callable(getattr(engine,'pair',None)),'Server pairing missing')
            result=engine.pair('10.0.0.2')
            self.assertTrue(engine.status()['paired'])
            self.assertEqual(engine.status()['areas'][0]['id'],'1')
            self.assertNotIn(USER,json.dumps(result));self.assertNotIn(KEY,json.dumps(engine.status()))
            saved=json.loads(cfg.read_text());self.assertEqual(saved['hue']['clientkey'],KEY)
            self.assertFalse(saved['enabled']);self.assertIsNone(saved['area_id'])
            if __import__('os').name!='nt': self.assertEqual(cfg.stat().st_mode&0o777,0o600)


    def test_transport_failure_restores_lights_and_reports_no_secrets_with_retry_backoff(self):
        class BrokenBridge(FixtureBridge):
            def send(self,data): raise RuntimeError(KEY)
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            engine=Engine(cfg,None,FixtureCache(),[],BrokenBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            self.assertEqual(engine.status()['phase'],'error');self.assertFalse(engine.status()['streaming'])
            self.assertEqual(engine.status()['error'], 'Synchronization interrupted. Check the bridge, area, and local transport.')
            self.assertNotIn(KEY,json.dumps(engine.status()));self.assertEqual(engine._bridge.releases,1)
            previous=engine._bridge;engine.tick(1);self.assertIs(engine._bridge,previous)


    def test_changing_area_releases_previous_stream_before_connecting_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            engine=Engine(cfg,None,FixtureCache(),[],FixtureBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            old=engine._bridge;engine.configure({'area_id':'2'});engine.tick(1)
            self.assertEqual(old.releases,1);self.assertFalse(old.active)
            self.assertEqual(engine._bridge.connections,['2']);self.assertTrue(engine.status()['streaming'])


    def test_cache_miss_releases_existing_stream_while_new_segment_is_prepared(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            cache=FixtureCache();engine=Engine(cfg,None,cache,[],FixtureBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            cache.get=lambda item,position:None
            engine.tick(1)
            self.assertEqual(engine.status()['phase'],'preparing');self.assertFalse(engine.status()['streaming'])
            self.assertEqual(engine._bridge.releases,1);self.assertEqual(engine.status()['frames_sent'],1)


    def test_responsiveness_smooths_real_color_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config.update(enabled=True,brightness=1.0,smoothing=0.5);cfg.write_text(json.dumps(config))
            cache=FixtureCache();engine=Engine(cfg,None,cache,[],FixtureBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            self.assertEqual(engine.status()['rgb'],[(50,0,0),(0,0,50)])
            engine.tick(0.05)
            self.assertEqual(engine.status()['rgb'],[(75,0,0),(0,0,75)])


    def test_settings_reject_private_fields_and_invalid_types_without_changing_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps(unit_config()))
            engine=Engine(cfg,None,FixtureCache(),[],FixtureBridge)
            original=cfg.read_bytes()
            cases=[{'hue':{'username':'injected'}},{'bridge_ip':'8.8.8.8'},{'enabled':1},{'area_id':'01'},{'area_id':'../1'},{'area_id':1},{'device_id':None},{'brightness':float('nan')},{'brightness':True},{'brightness':-1},{'smoothing':0},{'offset_ms':0.5},{'offset_ms':5001}]
            for changes in cases:
                with self.subTest(changes=changes),self.assertRaises(ValueError): engine.configure(changes)
            self.assertEqual(cfg.read_bytes(),original)


    def test_explicit_stop_persists_disabled_config_and_releases_on_next_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            engine=Engine(cfg,None,FixtureCache(),[],FixtureBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            self.assertTrue(callable(getattr(engine,'configure',None)),'Configuration control missing')
            engine.configure({'enabled':False});engine.tick(1)
            self.assertFalse(engine.status()['streaming'])
            self.assertEqual(engine.status()['phase'],'disabled')
            self.assertFalse(json.loads(cfg.read_text())['enabled'])
            self.assertEqual(engine._bridge.releases,1)


    def test_pause_releases_owned_stream_instead_of_leaving_last_color(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            engine=Engine(cfg,None,FixtureCache(),[],FixtureBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            bridge=engine._bridge
            engine.accept_sessions([fixture_session(True)],1,fixture_wall_time());engine.tick(1)
            self.assertFalse(bridge.active);self.assertEqual(bridge.releases,1)
            self.assertEqual(engine.status()['frames_sent'],1);self.assertIsNone(engine.status()['rgb'])

    def test_running_playback_sends_cached_colors_as_valid_hue_packets(self):
        from ambilight.hue import packet
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            cache=FixtureCache();engine=Engine(cfg,None,cache,[],FixtureBridge)
            engine.accept_sessions([fixture_session()],0,fixture_wall_time());engine.tick(0)
            self.assertEqual(engine.status()['phase'],'streaming')
            self.assertEqual(engine.status()['frames_sent'],1)
            self.assertEqual(engine._bridge.sent,[packet(0,[1,2],[(70,0,0),(0,0,70)])])
            self.assertEqual(cache.requests,[('unit-item',10)])
            self.assertNotIn(USER,json.dumps(engine.status()))
            engine.tick(0.05)
            self.assertEqual(engine._bridge.connections,['1'])
            self.assertEqual(engine.status()['frames_sent'],2)

    def test_paused_playback_is_tracked_without_touching_hue(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';config=unit_config();config['enabled']=True;cfg.write_text(json.dumps(config))
            cache=FixtureCache();engine=Engine(cfg,None,cache,[],bridge_factory=lambda *a,**k:self.fail('Paused playback must not connect to Hue'))
            self.assertTrue(callable(getattr(engine,'accept_sessions',None)),'Session integration missing')
            engine.accept_sessions([fixture_session(True)],0,fixture_wall_time());engine.tick(0)
            status=engine.status()
            self.assertEqual(status['phase'],'paused');self.assertEqual(status['playback']['position'],10)
            self.assertEqual(status['playback']['title'],'Unit video');self.assertFalse(status['streaming'])

    def test_status_never_exposes_bridge_credentials_or_private_owner_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps(unit_config()))
            engine=Engine(cfg,None,None,[])
            data=engine.status();text=json.dumps(data)
            self.assertNotIn(USER,text);self.assertNotIn(KEY,text)
            self.assertTrue(data['paired'])
            self.assertFalse(data['config']['enabled'])

if __name__=='__main__': unittest.main()
