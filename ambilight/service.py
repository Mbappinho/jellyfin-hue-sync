"""Independent sync daemon lifecycle."""
import threading
import time

def build_engine(state_dir,library_root,settings_path,transport_dir,jellyfin_url='http://127.0.0.1:8096',image=None,*,api_key_file=None,decoder='cpu',media_prefix='/media'):
    import os
    from pathlib import Path
    from .engine import Engine,DEFAULT
    from .cache import ClipCache
    from .jellyfin import JellyfinClient
    from .storage import atomic_json
    from .state_lock import StateLock
    state_lock=StateLock(state_dir)
    try:
        state=state_lock.state_dir
        config=state/'config.json'
        if not config.exists(): atomic_json(config,DEFAULT)
        dist=Path(transport_dir).resolve()
        import shutil
        home=os.environ.get('JAVA_HOME')
        java=str(Path(home)/'bin'/('java.exe' if os.name=='nt' else 'java')) if home else (shutil.which('java') or 'java')
        command=[java,'-Xms16m','-Xmx128m','-XX:MaxMetaspaceSize=96m','-cp',str(dist/'classes')+os.pathsep+str(dist/'lib'/'*'),'fr.theo.hueserver.StreamMain']
        client=JellyfinClient(settings_path,jellyfin_url,api_key_file=api_key_file)
        cache=ClipCache(state/'colors',library_root,mode=decoder,image=image,media_prefix=media_prefix)
        engine=Engine(config,client,cache,command)
        engine._state_lock=state_lock
        return engine
    except BaseException:
        state_lock.close()
        raise

def run(engine,server,stop_event):
    def worker():
        next_poll=next_areas=0
        while not stop_event.is_set():
            now=time.monotonic()
            if now>=next_poll:
                engine.poll();next_poll=time.monotonic()+1
            if stop_event.is_set(): break
            if now>=next_areas:
                try: engine.refresh_areas()
                except Exception: pass
                next_areas=time.monotonic()+15
            if stop_event.is_set(): break
            engine.tick(time.monotonic())
            stop_event.wait(0.05)
    http=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.2},name='admin-http',daemon=True)
    loop=threading.Thread(target=worker,name='sync-loop',daemon=True)
    http.start();loop.start()
    try: stop_event.wait()
    finally:
        # Fence worker/admin operations before HTTP shutdown can block.
        engine.begin_shutdown()
        try:
            server.shutdown();server.server_close();http.join(2)
        finally:
            loop.join(20)
            engine.close()
