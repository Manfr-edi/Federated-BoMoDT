"""Bounded service inspection and cleanup of generated scenario directories."""
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import subprocess
from .deployment import validate_mode
from .jobs import list_jobs, mode_lock


def service_status(mode):
    """Inspect container state only; this is not an ingestion readiness probe."""
    expected=7 if validate_mode(mode)=='monolithic' else 77
    try:
        result=subprocess.run(['docker','ps','--all','--filter',
            'label=com.docker.compose.project=bomodt-'+mode,'--format','{{json .}}'],
            capture_output=True,text=True,timeout=4,check=True)
        rows=[json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        running=sum(r.get('State')=='running' for r in rows)
        unhealthy=sum('(unhealthy)' in r.get('Status','') for r in rows)
        starting=sum('(health: starting)' in r.get('Status','') for r in rows)
        state=('stopped' if not running else 'ready' if running==expected and not unhealthy and not starting else 'partial')
        return dict(state=state,running=running,expected=expected,unhealthy=unhealthy,starting=starting)
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        return dict(state='unknown',running=None,expected=expected,error=str(error))


def saved_scenarios(root):
    """Only direct, real generated directories; never follow symlink targets."""
    base=Path(root)/'scenarios'
    if base.is_symlink() or not base.exists(): return []
    return sorted(p for p in base.iterdir() if not p.is_symlink() and p.is_dir()
                  and (p/'scenario.json').is_file())


def wipe_scenarios(root):
    # Both dashboards share scenarios. Lock both in a fixed order so a new UI
    # operation cannot start between the busy check and directory removal.
    with ExitStack() as stack:
        for mode in ('monolithic','federated'): stack.enter_context(mode_lock(mode))
        if any(j['status'] in ('running','starting') for mode in ('monolithic','federated') for j in list_jobs(mode)):
            raise RuntimeError('Wait for operations in both dashboards to finish before deleting scenarios.')
        paths=saved_scenarios(root)
        for path in paths: shutil.rmtree(path)
        return len(paths)
