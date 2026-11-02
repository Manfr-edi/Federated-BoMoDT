"""One catalogue entry per operation, never per generated report directory."""
from datetime import datetime, timezone
import json
from pathlib import Path


def read(path):
    try: return json.loads(path.read_text())
    except (OSError,ValueError): return {}


def measurement_catalog(root,kind):
    root=Path(root)
    jobs={}
    for path in (root/'state/jobs').glob('*/*/job.json'):
        job=read(path);args=job.get('arguments',[])
        if job.get('kind')=='simulate' and '--output' in args:
            try:
                folder=Path(args[args.index('--output')+1])
                if not folder.is_absolute(): folder=root/folder
                jobs[folder.resolve()]=job
            except IndexError: continue
    if kind=='simulation':
        # Intentionally scan only operation folders. metrics/summary.json is an
        # export of the same run, not another simulation.
        folders={p.parent for name in ['summary.json','scenario-run.json','failure.json']
                 for p in (root/'experiments').glob('*/'+name)}
    else: folders={p.parent for p in (root/'sessions').glob('*/*/manifest.json')}
    result=[]
    for folder in folders:
        data=read(folder/('summary.json' if kind=='simulation' else 'manifest.json'))
        run=read(folder/'scenario-run.json') if kind=='simulation' else {}
        exported=read(folder/'metrics/summary.json')
        config=data.get('config',{})
        mode=run.get('mode') or data.get('mode') or config.get('mode')
        if mode not in ('monolithic','federated'): continue
        if kind=='simulation' and (config.get('demo') or not (run or config.get('routes') or data.get('scenario'))): continue
        job=jobs.get(folder.resolve(),{})
        origin='dashboard' if job or exported.get('dashboard_included') else 'cli'
        status='failed' if (folder/'failure.json').exists() or job.get('status') in ('failed','cancelled','interrupted') else data.get('status','unknown')
        integrated=run.get('integrated',bool(config.get('fiware_tenant')))
        created=job.get('created_at')
        if created is None and data.get('created_at'):
            try: created=datetime.fromisoformat(data['created_at'].replace('Z','+00:00')).timestamp()
            except (ValueError,TypeError): pass
        if created is None:
            try: created=datetime.strptime(folder.name[:15],'%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc).timestamp()
            except ValueError: created=folder.stat().st_mtime
        result.append(dict(folder=folder,data=data,mode=mode,kind=kind,status=status,origin=origin,
            integrated=integrated,created=created,control=run.get('control',config.get('control',data.get('control'))),
            scenario=run.get('scenario',data.get('scenario'))))
    return sorted(result,key=lambda r:(r['created'],r['folder'].name),reverse=True)


def select_measurements(records,modes,include_cli=False,include_failed=False,include_offline=False):
    return [r for r in records if r['mode'] in modes
        and (include_cli or r['origin']=='dashboard')
        and (include_failed or r['status'] in ('completed','verified'))
        and (r['kind']!='simulation' or include_offline or r['integrated'])]


def measurement_label(record):
    timestamp=datetime.fromtimestamp(record['created'],timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    control={'none':'No agents','local':'Local agents','coordinated':'Locals + coordinator'}.get(record['control'],'Emulation')
    extra=[]
    if record['origin']=='cli': extra.append('CLI')
    if record['kind']=='simulation' and not record['integrated']: extra.append('Offline')
    if record['status'] not in ('completed','verified'): extra.append(record['status'])
    suffix=' · '+' / '.join(extra) if extra else ''
    return f"{record['mode'].title()} · {timestamp} · {control} · {record['folder'].name}{suffix}"
