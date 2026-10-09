"""Bounded asynchronous color cache; source files are never modified."""
from pathlib import Path
from .extraction import ColorChunk, resolve_media

class ClipCache:
    extraction_timeout=120.0
    max_bytes=64*1024*1024
    max_chunks=4096
    def __init__(self,cache_root,library_root,mode='cpu',image=None,extractor=None,media_prefix='/media'):
        import threading
        self.cache_root=Path(cache_root);self.library_root=Path(library_root);self.mode=mode;self.image=image;self.extractor=extractor;self.media_prefix=media_prefix
        self._validate_cache_root()
        self._lock=threading.Condition();self._pending=[];self._stop=False;self._thread=None;self._container=None
        self._process=None
        self._protected=set()
        self._status={'phase':'idle','frames':0,'mode':mode,'error':None}
        self._prune()

    def _validate_cache_root(self):
        cache=self.cache_root.resolve();library=self.library_root.resolve()
        if cache.is_relative_to(library) or library.is_relative_to(cache):
            raise ValueError('Color cache and source library must not overlap')

    @property
    def status(self):
        with self._lock: return dict(self._status)
    def request(self,item,position):
        import math,threading,copy
        if not math.isfinite(position) or position<0: raise ValueError('Invalid position')
        resolve_media(item['Path'],self.library_root,self.media_prefix)
        start=math.floor(position/30)*30
        runtime=item.get('RunTimeTicks',0)/1e7
        with self._lock:
            desired=[]
            self._protected=set()
            for offset in (start,start+30):
                if runtime and offset>=runtime: continue
                path=self._path(item,offset);self._protected.add(path)
                if not path.exists(): desired.append((copy.deepcopy(item),offset))
            self._prune(self._protected)
            self._pending=desired
            if self._thread is None:
                self._thread=threading.Thread(target=self._work,name='color-extraction',daemon=True);self._thread.start()
            self._lock.notify_all()
    def stop(self):
        import subprocess
        with self._lock:
            self._stop=True;self._pending=[];name=self._container;process=self._process;self._lock.notify_all()
        if process is not None:
            try: process.kill()
            except OSError: pass
        if name:
            try: subprocess.run(['docker','kill',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
            except Exception: pass
        if self._thread is not None: self._thread.join(10)
    def _progress(self,count):
        with self._lock: self._status['frames']=count
    def _work(self):
        while True:
            with self._lock:
                self._lock.wait_for(lambda:self._pending or self._stop)
                if self._stop: return
                item,start=self._pending.pop(0)
                self._status.update(phase='extracting',start=start,frames=0,error=None)
            try:
                if not self._path(item,start).exists():
                    frames=(self.extractor or self._extract)(item,start,self._progress)
                    if not frames: raise ValueError('No decoded frames')
                    self.put(item,start,frames)
                with self._lock: self._status.update(phase='ready',error=None)
            except Exception as error:
                with self._lock: self._status.update(phase='error',error='Video analysis interrupted ('+type(error).__name__+').')
    def _extract(self,item,start,progress):
        import subprocess,threading,uuid
        from .extraction import make_ffmpeg_command,decode_rgb_stream
        resolve_media(item['Path'],self.library_root,self.media_prefix)
        hdr=any(stream.get('Type')=='Video' and stream.get('ColorTransfer') in ('smpte2084','arib-std-b67') for stream in item.get('MediaStreams',[]))
        duration=min(30,max(.25,item.get('RunTimeTicks',int((start+30)*1e7))/1e7-start))
        for mode in (['vaapi','cpu'] if self.mode=='vaapi' else ['cpu']):
            if self._stop: raise RuntimeError('Extractor stopped')
            name='hue-colors-'+uuid.uuid4().hex[:16]
            command=make_ffmpeg_command(item['Path'],self.library_root,start,duration,hdr,mode,name,self.image,self.media_prefix)
            with self._lock: self._container=name
            try:
                process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            except Exception:
                with self._lock: self._container=None
                raise
            with self._lock:
                self._process=process
                if self._stop: process.kill()
            expired=threading.Event()
            def terminate():
                # The local CLI owns stdout. Unblock it even if Docker is unavailable.
                try: process.kill()
                except OSError: pass
                try: subprocess.run(['docker','kill',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
                except Exception: pass
            def expire():
                expired.set();terminate()
            timer=threading.Timer(self.extraction_timeout,expire);timer.daemon=True
            timer.start()
            try:
                def frame_progress(count):
                    if count>duration*4+4: raise ValueError('Too many output frames')
                    progress(count)
                frames=decode_rgb_stream(process.stdout,progress=frame_progress)
                code=process.wait(timeout=8)
                if expired.is_set(): raise TimeoutError('Video analysis deadline exceeded')
                if self._stop: raise RuntimeError('Extractor stopped')
                if code==0 and frames:
                    with self._lock: self._status['mode']=mode
                    return frames
            except Exception:
                if not expired.is_set(): terminate()
                process.wait(timeout=8)
                raise
            finally:
                timer.cancel();process.stdout.close()
                with self._lock: self._container=None;self._process=None
            if mode=='vaapi':
                self.mode='cpu'
                with self._lock: self._status['mode']='cpu'
        raise RuntimeError('FFmpeg failed; no colors generated')
    def _key(self,item):
        import hashlib,json
        source=resolve_media(item['Path'],self.library_root,self.media_prefix); info=source.stat()
        identity=json.dumps([item.get('Id'),str(source),info.st_size,info.st_mtime_ns,item.get('MediaStreams',[]),'rgb4-hdr-hable-v1'],sort_keys=True)
        return hashlib.sha256(identity.encode()).hexdigest()
    def _path(self,item,position):
        import math
        return self.cache_root/(self._key(item)+'-'+str(math.floor(position/30))+'.json')
    def put(self,item,start,frames):
        from .storage import atomic_json
        # At most one 30-second RGB4 window plus the decoder's four-frame margin.
        # This also bounds the two protected chunks independently of eviction.
        if not isinstance(frames,(list,tuple)) or not 0<len(frames)<=124:
            raise ValueError('Invalid color chunk')
        for frame in frames:
            if not isinstance(frame,(list,tuple)) or len(frame)!=2:
                raise ValueError('Invalid color chunk')
            for rgb in frame:
                if (not isinstance(rgb,(list,tuple)) or len(rgb)!=3 or
                        any(type(channel) is not int or not 0<=channel<=255 for channel in rgb)):
                    raise ValueError('Invalid color chunk')
        with self._lock:
            self._validate_cache_root()
            path=self._path(item,start)
            atomic_json(path,{'version':1,'start':start,'fps':4,'frames':frames})
            self._prune(self._protected or {path})
    def _prune(self,protected=()):
        import re,stat
        self._validate_cache_root()
        if not self.cache_root.exists(): return
        entries=[]
        for path in self.cache_root.iterdir():
            if not re.fullmatch(r'[a-f0-9]{64}-[0-9]+\.json',path.name): continue
            try: info=path.lstat()
            except FileNotFoundError: continue
            if not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0): continue
            entries.append((info.st_mtime_ns,path,info.st_size))
        total=sum(entry[2] for entry in entries)
        count=len(entries)
        for _,path,size in sorted(entries):
            if total<=self.max_bytes and count<=self.max_chunks: break
            if path in protected: continue
            try: path.unlink()
            except FileNotFoundError: pass
            total-=size;count-=1
    def get(self,item,position):
        from .storage import read_json
        with self._lock:
            path=self._path(item,position)
            if not path.exists(): return None
            data=read_json(path)
            return ColorChunk(data['start'],data['fps'],data['frames']).at(position)
