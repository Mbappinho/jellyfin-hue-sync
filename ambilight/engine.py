"""Independent server sync engine. No Android screen capture or Jellyfin writes."""
import copy
import threading
from pathlib import Path
from .core import PlaybackClock
from .hue import HueBridge
from .storage import read_json

DEFAULT={'enabled':False,'device_id':'','area_id':None,'brightness':0.7,'smoothing':0.3,'offset_ms':0,'bridge_ip':'','hue':None}
PUBLIC_FIELDS=('enabled','device_id','area_id','brightness','smoothing','offset_ms')

class Engine:
    CLOSE_TIMEOUT=20.0

    def __init__(self,config_path,client,cache,java_command,bridge_factory=None):
        self.config_path=Path(config_path)
        self.client=client;self.cache=cache;self.java_command=java_command
        self.bridge_factory=bridge_factory or HueBridge
        self._lock=threading.RLock();self._hue_lock=threading.RLock()
        self._cancel=threading.Event()
        self._close_lock=threading.Lock();self._close_done=threading.Event()
        self._close_started=False;self._close_result=None;self._state_lock=None
        self._cfg=copy.deepcopy(DEFAULT)
        if self.config_path.exists(): self._cfg.update(read_json(self.config_path))
        self._clock=PlaybackClock();self._item=None;self._areas=[];self._devices=[]
        self._bridge=None;self._connected=False;self._stream_signature=None
        self._sequence=0;self._previous_rgb=None;self._retry_after=0
        self._test_requested=False;self._test_until=0
        self._state={'phase':'unpaired' if not self._cfg.get('hue') else 'disabled',
                     'error':None,'frames_sent':0,'rgb':None,
                     'playback':{'title':None,'position':None,'paused':False,'device':None}}
    def configure(self,changes):
        from .storage import atomic_json
        import math,re
        if not isinstance(changes,dict) or not set(changes)<=set(PUBLIC_FIELDS):
            raise ValueError('Settings are not allowed.')
        if 'enabled' in changes and type(changes['enabled']) is not bool:
            raise ValueError('Invalid enabled setting.')
        if 'device_id' in changes and (not isinstance(changes['device_id'],str) or len(changes['device_id'])>200):
            raise ValueError('Invalid device ID.')
        if 'area_id' in changes and changes['area_id'] is not None and (not isinstance(changes['area_id'],str) or not re.fullmatch(r'[1-9][0-9]{0,5}',changes['area_id'])):
            raise ValueError('Invalid Entertainment area.')
        for field,minimum in (('brightness',0),('smoothing',0.01)):
            if field in changes and (type(changes[field]) not in (int,float) or not math.isfinite(changes[field]) or not minimum<=changes[field]<=1):
                raise ValueError('Invalid color setting.')
        if 'offset_ms' in changes and (type(changes['offset_ms']) is not int or not -5000<=changes['offset_ms']<=5000):
            raise ValueError('Invalid offset.')
        with self._lock:
            self._require_running()
            updated=copy.deepcopy(self._cfg);updated.update(changes)
            if updated['enabled'] and (not updated.get('hue') or not updated['area_id'] or not updated['device_id']):
                raise ValueError('Pair the bridge, then choose an area and a device.')
            atomic_json(self.config_path,updated)
            if updated['device_id']!=self._cfg['device_id']:
                self._clock=PlaybackClock();self._item=None
                self._state['playback']={'title':None,'position':None,'paused':False,'device':None}
            self._cfg=updated
            if changes.get('enabled') is False:
                self._test_requested=False;self._test_until=0
            self._retry_after=0
        return self.status()

    def status(self):
        with self._lock:
            result=copy.deepcopy(self._state)
            result.update(paired=bool(self._cfg.get('hue')),bridge_ip=self._cfg['bridge_ip'],
                          config={k:self._cfg[k] for k in PUBLIC_FIELDS},
                          areas=copy.deepcopy(self._areas),devices=copy.deepcopy(self._devices),
                          streaming=bool(self._connected and self._bridge and self._bridge.active),
                          cache={} if self.cache is None else self.cache.status)
            return result

    def accept_sessions(self,sessions,now,wall_time=None):
        from .jellyfin import choose_session
        with self._lock:
            if self._cancel.is_set(): return
            session=choose_session(sessions,self._cfg['device_id'],wall_time)
            self._clock.update(session,now)
            self._item=copy.deepcopy(session.get('NowPlayingItem')) if session else None
            self._devices=[{'id':s['DeviceId'],'name':str(s.get('DeviceName','Device'))[:200],
                            'client':str(s.get('Client',''))[:100],
                            'active':bool(s.get('NowPlayingItem'))} for s in sessions if isinstance(s.get('DeviceId'),str)]
            self._state['playback']={'title':self._item.get('Name') if self._item else None,
                                     'position':self._clock.position(now),
                                     'paused':self._clock.paused,
                                     'device':session.get('DeviceName') if session else None}

            position=self._state['playback']['position']
            if self._cfg['enabled'] and not self._clock.paused and position is not None:
                self.cache.request(self._item,max(0,position-self._cfg['offset_ms']/1000))

    def poll(self,now=None,wall_time=None):
        import time
        if self._cancel.is_set(): return
        try:
            sessions=self.client.sessions()
            if self._cancel.is_set(): return
            self.accept_sessions(sessions,time.monotonic() if now is None else now,wall_time)
            with self._lock:
                if self._cancel.is_set(): return
                if self._state['error']=='Jellyfin is unavailable.': self._state['error']=None
        except Exception:
            if self._cancel.is_set(): return
            self.accept_sessions([],time.monotonic() if now is None else now,wall_time)
            with self._lock:
                if not self._cancel.is_set(): self._state.update(phase='error',error='Jellyfin is unavailable.')

    def _public_areas(self,raw,cfg):
        return [{**{k:a[k] for k in ('id','name','ids','xs','active')},
                 'occupied':a['active'] and a['owner']!=cfg['hue']['username']} for a in raw]

    def pair(self,ip):
        from .storage import atomic_json
        with self._hue_lock:
            self._require_running()
            self._release()
            self._require_running()
            with self._lock: cfg=copy.deepcopy(self._cfg)
            if cfg.get('hue'):
                previous=self.bridge_factory(**cfg['hue'],journal_path=self.config_path.parent/'restore.json')
                self._require_running()
                previous.recover()
                self._require_running()
            bridge=self.bridge_factory(ip)
            self._require_running()
            credentials=bridge.pair()
            self._require_running()
            raw=bridge.areas()
            with self._lock:
                self._require_running()
                updated=copy.deepcopy(self._cfg)
                updated.update(hue=credentials,bridge_ip=credentials['ip'],enabled=False,area_id=None)
                atomic_json(self.config_path,updated);self._cfg=updated
                self._areas=self._public_areas(raw,updated)
                self._state.update(phase='disabled',error=None)
                self._retry_after=0;self._bridge=None
        return self.status()

    def refresh_areas(self):
        if self._cancel.is_set(): return []
        with self._hue_lock:
            if self._cancel.is_set(): return []
            with self._lock: cfg=copy.deepcopy(self._cfg)
            if not cfg.get('hue'): return []
            if self._connected:
                bridge=self._bridge
            else:
                bridge=self.bridge_factory(**cfg['hue'],journal_path=self.config_path.parent/'restore.json')
                if self._cancel.is_set(): return []
                bridge.recover()
            if self._cancel.is_set(): return []
            areas=self._public_areas(bridge.areas(),cfg)
            with self._lock:
                if self._cancel.is_set(): return []
                self._areas=areas
            return copy.deepcopy(areas)

    def _signature(self,cfg):
        hue=cfg.get('hue') or {}
        return (hue.get('ip'),hue.get('username'),hue.get('clientkey'),cfg['area_id'])

    def _ensure_stream(self,cfg):
        self._require_running()
        signature=self._signature(cfg)
        if self._connected and signature==self._stream_signature and self._bridge.active:
            return self._selected_area
        self._release()
        self._require_running()
        self._bridge=self.bridge_factory(**cfg['hue'],java_command=self.java_command,
                                          journal_path=self.config_path.parent/'restore.json')
        self._require_running()
        self._bridge.recover()
        self._require_running()
        # Mark even a partial connection as requiring cleanup.
        self._connected=True
        self._bridge.connect(cfg['area_id'])
        self._require_running()
        raw=self._bridge.areas()
        self._require_running()
        area=next((a for a in raw if a['id']==cfg['area_id']),None)
        if area is None or not area['active'] or area['owner']!=cfg['hue']['username']:
            raise RuntimeError('Entertainment area ownership changed.')
        self._selected_area=area;self._stream_signature=signature
        with self._lock:
            self._areas=self._public_areas(raw,cfg)
        return area

    def _release(self):
        with self._hue_lock:
            if self._connected:
                self._bridge.release();self._connected=False
                self._stream_signature=None;self._previous_rgb=None
            with self._lock: self._state['rgb']=None

    def tick(self,now):
        try:
            self._tick(now)
        except Exception:
            try: self._release()
            except Exception: pass
            with self._lock:
                if self._cancel.is_set(): return
                self._state.update(phase='error',error='Synchronization interrupted. Check the bridge, area, and local transport.')
                self._retry_after=now+10

    def _require_running(self):
        if self._cancel.is_set(): raise RuntimeError('Engine is shutting down.')

    def begin_shutdown(self):
        """Cancel immediately, without waiting for blocking network operations."""
        self._cancel.set()

    def close(self,timeout=None):
        """Bound the caller's wait; retain the lifetime lock until cleanup finishes.

        Python cannot kill a thread inside external I/O. A timed-out close is
        explicitly incomplete, not permission for another service to recover
        its journal. The canceled finalizer owns resources until safe cleanup.
        """
        self.begin_shutdown()
        with self._close_lock:
            if not self._close_started or (self._close_done.is_set() and self._close_result['error']):
                if self._close_started: self._close_done=threading.Event()
                self._close_started=True
                self._close_result={'done':self._close_done,'error':False}
                threading.Thread(target=self._finish_close,args=(self._close_result,),name='sync-cleanup',daemon=True).start()
            # Each caller observes its own immutable completion generation.
            result=self._close_result
        if not result['done'].wait(self.CLOSE_TIMEOUT if timeout is None else timeout):
            raise TimeoutError('Engine shutdown is still pending; state directory remains locked.')
        if result['error']:
            raise RuntimeError('Engine shutdown incomplete; state directory remains locked.')

    def _finish_close(self,result):
        try:
            with self._hue_lock:
                try: self._release()
                finally:
                    if self.cache is not None: self.cache.stop()
                # ClipCache.stop has a bounded join. A live extractor may still
                # write shared state: a timeout must not relinquish ownership.
                worker=getattr(self.cache,'_thread',None)
                if worker is not None and worker.is_alive():
                    raise RuntimeError('Color extraction is still stopping.')
                if self._state_lock is not None: self._state_lock.close()
        except BaseException:
            # Fail closed: do not hand a live journal to another instance.
            result['error']=True
        finally:
            result['done'].set()

    def test_lights(self):
        with self._lock:
            self._require_running()
            if not self._cfg.get('hue') or not self._cfg['area_id']:
                raise ValueError('Pair the bridge and choose an area before testing.')
            self._test_requested=True;self._retry_after=0
        return self.status()

    def _tick(self,now):
        if self._cancel.is_set(): return
        from .core import colors_for_lights
        from .hue import packet
        with self._lock:
            if self._cancel.is_set(): return
            position=self._clock.position(now);self._state['playback']['position']=position
            cfg=copy.deepcopy(self._cfg);item=copy.deepcopy(self._item)
            if self._test_requested:
                self._test_requested=False;self._test_until=now+4
            testing=now<self._test_until
            phase=('unpaired' if not cfg.get('hue') else 'disabled' if not cfg['enabled'] else
                   'paused' if self._clock.paused else 'waiting' if position is None else None)
            if testing: phase=None
            if phase is None and now<self._retry_after: phase='error'
            if phase: self._state['phase']=phase
        if phase:
            self._release();return
        if self._cancel.is_set(): return
        frame=[(190,65,15),(15,55,190)] if testing else self.cache.get(item,max(0,position-cfg['offset_ms']/1000))
        if self._cancel.is_set(): return
        if frame is None:
            with self._lock:
                if self._cancel.is_set(): return
                self._state['phase']='preparing'
            self._release();return
        with self._hue_lock:
            if self._cancel.is_set(): return
            area=self._ensure_stream(cfg)
            with self._lock: cancelled=self._cancel.is_set() or cfg!=self._cfg or (testing and self._test_until<=now)
            if cancelled:
                self._release();return
            rgb=colors_for_lights(frame[0],frame[1],area['xs'],cfg['brightness'])
            previous=self._previous_rgb or [(0.0,0.0,0.0)]*len(rgb)
            alpha=cfg['smoothing']
            self._previous_rgb=[tuple(old+(new-old)*alpha for old,new in zip(a,b)) for a,b in zip(previous,rgb)]
            rgb=[tuple(round(c) for c in color) for color in self._previous_rgb]
            self._require_running()
            self._bridge.send(packet(self._sequence,area['ids'],rgb));self._sequence=(self._sequence+1)&255
            with self._lock:
                if self._cancel.is_set(): return
                self._state.update(phase='testing' if testing else 'streaming',error=None,rgb=rgb)
                self._state['frames_sent']+=1
