"""Local-only native UI verification server. Docker publishes loopback ports."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    evidence=Path('/evidence'); evidence.mkdir(exist_ok=True)
    logs=[(evidence/name).open('w') for name in ('backend.log','frontend.log')]
    env=dict(os.environ,BACKEND_URL='http://127.0.0.1:8000',STREAMLIT_BROWSER_GATHER_USAGE_STATS='false')
    # UI verification never uses provider credentials. Live V15 is a separate run.
    for key in list(env):
        if key.startswith('GEMINI_API_KEY') or key=='GOOGLE_APPLICATION_CREDENTIALS': env.pop(key)
    commands=[[sys.executable,'-m','uvicorn','main:app','--host','0.0.0.0','--port','8000'],
        [sys.executable,'-m','streamlit','run','frontend/app.py','--server.address=0.0.0.0','--server.port=8501','--server.headless=true']]
    children=[subprocess.Popen(command,env=env,stdout=log,stderr=log) for command,log in zip(commands,logs)]
    stopped=False
    def stop(*args):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    try:
        while not stopped and all(child.poll() is None for child in children): time.sleep(.2)
    finally:
        for child in children: child.terminate()
        for child in children:
            try: child.wait(timeout=15)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        for log in logs: log.close()


if __name__=='__main__': main()
