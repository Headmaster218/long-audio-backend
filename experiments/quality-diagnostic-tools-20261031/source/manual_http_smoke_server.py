"""Real app lifespan for external, private one-shot lifecycle smoke; no mocks."""
import json,signal,socket,sys
from pathlib import Path

def main(spec_path,out_path):
    spec=json.loads(Path(spec_path).read_text()); out=Path(out_path)
    sys.path.insert(0,spec['repo'])
    from audio_backend.settings import Settings
    from audio_backend.app import create_app
    settings=dict(spec['settings'])
    for key in ('data_dir','model_dir'):settings[key]=Path(settings[key])
    app=create_app(Settings(**settings))
    serve(app,out)

def serve(app,out):
    import uvicorn
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    (out/'endpoint.json').write_text(json.dumps({'host':'127.0.0.1','port':sock.getsockname()[1]}))
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',log_level='info',timeout_graceful_shutdown=55))
    # Uvicorn restores and re-raises SIGTERM after graceful shutdown.
    # A wrapper handler allows our finally evidence to be written; no app override.
    def terminated(signum,frame):raise SystemExit(0)
    signal.signal(signal.SIGTERM,terminated)
    try:server.run(sockets=[sock])
    finally:
        state=app.state
        evidence={key:getattr(state,key,None) for key in ('instance_lock_lifespan_started','instance_lock_cleanup_complete','shutdown_blocked','shutdown_decision_diagnostics')}
        (out/'shutdown.json').write_text(json.dumps(evidence,indent=2))
        sock.close()

if __name__=='__main__':main(*sys.argv[1:])
