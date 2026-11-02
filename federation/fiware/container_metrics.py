"""Capture Docker resource snapshots separately from the application process tree."""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import threading
import time
from .deployment import compose_command


@contextmanager
def monitor_containers(mode, output):
    samples=[]
    stop=threading.Event()
    def sample():
        while not stop.is_set():
            try:
                command=compose_command(mode,'stats')
                result=subprocess.run(command,capture_output=True,text=True,timeout=10)
                samples.append(dict(wall_ns=time.perf_counter_ns(),returncode=result.returncode,
                    docker_stats=result.stdout,error=result.stderr))
                if result.returncode: break
            except Exception as error:
                samples.append(dict(wall_ns=time.perf_counter_ns(),error=str(error)));break
            stop.wait(2)
    thread=threading.Thread(target=sample,daemon=True);thread.start()
    try: yield
    finally:
        stop.set();thread.join(12)
        output=Path(output)
        if output.exists():
            (output/'fiware-resources.json').write_text(json.dumps(dict(mode=mode,samples=samples),indent=2))
