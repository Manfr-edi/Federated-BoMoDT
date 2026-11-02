"""Durable background jobs so Streamlit reruns never restart a running operation."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from ..models import ROOT
from .deployment import validate_mode
from .emulator import save

JOBS=ROOT/'state/jobs'
_CHILDREN={}


def process_token(pid):
    """Linux process start time prevents accidentally signalling a reused PID."""
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except (FileNotFoundError,ProcessLookupError,PermissionError): return None


@contextmanager
def mode_lock(mode):
    directory=JOBS/validate_mode(mode)
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'.lock').open('w') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        try: yield directory
        finally: fcntl.flock(handle,fcntl.LOCK_UN)


def list_jobs(mode):
    for identifier,process in list(_CHILDREN.items()):
        if process.poll() is not None: _CHILDREN.pop(identifier,None)
    jobs=[]
    for path in sorted((JOBS/validate_mode(mode)).glob('*/job.json'),reverse=True):
        try:
            item=json.loads(path.read_text());item['path']=str(path)
            if item['status']=='running' and process_token(item['pid'])!=item['process_token']:
                item['status']='interrupted'
                save(path,{k:v for k,v in item.items() if k!='path'})
            if item['status']=='starting' and time.time()-item['created_at']>10:
                launcher=path.with_name('launcher.json')
                process=json.loads(launcher.read_text()) if launcher.exists() else {}
                if not process or process_token(process['pid'])!=process['process_token']:
                    item['status']='interrupted'
                    save(path,{k:v for k,v in item.items() if k!='path'})
            jobs.append(item)
        except (FileNotFoundError,json.JSONDecodeError): continue
    return jobs


def start_job(mode, kind, arguments):
    if kind not in ('stack','emulate','prepare','simulate'): raise ValueError('Unknown job type')
    # Always invoke a known module with an argument vector; no shell interpolation.
    if not arguments or arguments[0] not in ('stack','emulate','prepare','simulate'):
        raise ValueError('Unsupported background operation')
    with mode_lock(mode) as directory:
        # Serialize mutations within one deployment. Independent modes can run concurrently.
        if any(j['status'] in ('starting','running') for j in list_jobs(mode)):
            raise RuntimeError('An operation is already running in this dashboard')
        identifier=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]
        target=directory/identifier;target.mkdir()
        path=target/'job.json'
        item=dict(id=identifier,mode=mode,kind=kind,arguments=arguments,status='starting',created_at=time.time())
        save(path,item)
        try:
            with (target/'job.log').open('wb') as log:
                process=subprocess.Popen([sys.executable,'-u','-m','federation.fiware.jobs',str(path)],
                    cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
                    env={**os.environ,'BOMODT_DASHBOARD_PID':str(os.getpid())})
            _CHILDREN[identifier]=process
            save(target/'launcher.json',dict(pid=process.pid,process_token=process_token(process.pid)))
            # The runner owns future updates. The caller does not race its state writes.
            return identifier
        except BaseException as error:
            item.update(status='failed',error=str(error));save(path,item);raise


def stop_job(item):
    if item['status']!='running': raise ValueError('Job is not running')
    pid=item['pid']
    if process_token(pid)!=item['process_token']: raise RuntimeError('Job process has already exited')
    os.killpg(pid,signal.SIGTERM)


def tail_log(item, limit=12000):
    path=Path(item['path']).with_name('job.log')
    if not path.exists(): return ''
    with path.open('rb') as handle:
        handle.seek(max(0,path.stat().st_size-limit))
        return handle.read().decode(errors='replace')


def worker(path):
    path=Path(path);item=json.loads(path.read_text())
    item.update(status='running',pid=os.getpid(),process_token=process_token(os.getpid()),started_at=time.time())
    save(path,item)
    def cancelled(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,cancelled)
    try:
        result=subprocess.run([sys.executable,'-u','-m','federation.fiware',*item['arguments']],cwd=ROOT)
        item.update(status='completed' if result.returncode==0 else 'failed',returncode=result.returncode)
    except KeyboardInterrupt: item.update(status='cancelled')
    except BaseException as error: item.update(status='failed',error=str(error))
    finally:
        item['finished_at']=time.time();save(path,item)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('path');worker(parser.parse_args().path)
