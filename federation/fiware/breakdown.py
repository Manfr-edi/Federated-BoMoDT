"""Physical process/container cost and separately attributed logical policy work."""
import json
from pathlib import Path


def load(path,default):
    return json.loads(path.read_text()) if path.exists() else default


def component_costs(folder):
    from .reports import process_capture, docker_rows, bytes_value
    folder=Path(folder)
    registry={(r['pid'],r['create_time']):r for r in load(folder/'process-components.json',[])}
    totals={};previous={}
    for sample in load(process_capture(folder),{}).get('samples',[]):
        for p in sample['processes']:
            identity=(p['pid'],p.get('create_time'));registration=registry.get(identity,{})
            label=registration.get('component') or ('Dashboard' if p.get('role')=='dashboard' else
                'SUMO physics' if 'sumo' in p.get('name','').lower() else 'Unattributed runtime')
            key=('process',identity)
            row=totals.setdefault(key,dict(scope='process',component=label,pid=p['pid'],taz=registration.get('taz'),
                sampled_cpu_seconds=0.,peak_memory_mib=0.,peak_uss_mib=None,samples=0))
            row['peak_memory_mib']=max(row['peak_memory_mib'],p['rss']/1024**2)
            if p.get('uss') is not None: row['peak_uss_mib']=max(row['peak_uss_mib'] or 0,p['uss']/1024**2)
            if identity in previous: row['sampled_cpu_seconds']+=max(0,p['cpu_seconds']-previous[identity])
            previous[identity]=p['cpu_seconds'];row['samples']+=1
    last_times={}
    for sample in load(folder/'fiware-resources.json',{}).get('samples',[]):
        if sample.get('returncode',0)!=0: continue
        for c in docker_rows(sample.get('docker_stats')):
            name=c.get('Name',c.get('Container','unknown'));key=('container',name)
            row=totals.setdefault(key,dict(scope='container',component=name,pid=None,taz=None,
                service=name.rsplit('-',2)[-2] if '-' in name else name,
                sampled_cpu_seconds=0.,peak_memory_mib=None,peak_uss_mib=None,samples=0))
            memory=bytes_value(str(c.get('MemUsage','')).split('/')[0])
            if memory is not None: row['peak_memory_mib']=max(row['peak_memory_mib'] or 0,memory/1024**2)
            try: cpu=float(str(c.get('CPUPerc','')).strip('%'))
            except ValueError: cpu=None
            if name in last_times and cpu is not None:
                row['sampled_cpu_seconds']+=cpu/100*(sample['wall_ns']-last_times[name])/1e9
            last_times[name]=sample['wall_ns'];row['samples']+=1
    for row in totals.values():
        if row['samples']<2: row['sampled_cpu_seconds']=None
    return list(totals.values())


def policy_work(folder):
    folder=Path(folder);summary=load(folder/'summary.json',{})
    ids=summary.get('taz_ids',[]);totals={}
    mode=summary.get('config',{}).get('mode',summary.get('mode'))
    path=folder/'cycles.jsonl'
    if not path.exists(): return []
    for line in path.read_text().splitlines():
        record=json.loads(line)
        for i,wall in enumerate(record.get('local_inference_ms',[])):
            taz=ids[i] if i<len(ids) else str(i)
            row=totals.setdefault(taz,dict(taz=taz,decisions=0,total_inference_wall_ms=0.,total_inference_cpu_ms=None,
                execution_component='Central runtime (shared)' if mode=='monolithic' else 'Local worker / '+taz if mode=='federated' else 'Unspecified placement'))
            row['decisions']+=1;row['total_inference_wall_ms']+=wall
            cpu=record.get('local_inference_cpu_ms',[])
            if i<len(cpu): row['total_inference_cpu_ms']=(row['total_inference_cpu_ms'] or 0)+cpu[i]
    for row in totals.values():
        row['mean_inference_wall_ms']=row['total_inference_wall_ms']/row['decisions']
        row['mean_inference_cpu_ms']=row['total_inference_cpu_ms']/row['decisions'] if row['total_inference_cpu_ms'] is not None else None
    return list(totals.values())


def role_costs(folder):
    """Aggregate memory within a sample, then take peaks; never add PID peaks."""
    from .reports import process_capture
    folder=Path(folder)
    registry={(r['pid'],r['create_time']):r for r in load(folder/'process-components.json',[])}
    physical=component_costs(folder);roles={};labels={}
    def label(process):
        entry=registry.get((process['pid'],process.get('create_time')), {})
        raw=entry.get('component','')
        if raw.startswith('Central runtime'): return 'Central control runtime'
        if raw.startswith('Global runtime'): return 'Coordinator and world runtime'
        if raw.startswith('Local worker'): return 'TAZ '+entry['taz']+' controller'
        if process.get('role')=='dashboard': return 'Streamlit dashboard'
        if 'sumo' in process.get('name','').lower(): return 'SUMO physics'
        return 'Other runtime support (role not recorded)'
    for sample in load(process_capture(folder),{}).get('samples',[]):
        grouped={}
        for p in sample['processes']:
            name=label(p);labels[(p['pid'],p.get('create_time'))]=name
            group=grouped.setdefault(name,[]);group.append(p)
        for name,group in grouped.items():
            row=roles.setdefault(name,dict(component=name,sampled_cpu_seconds=None,peak_memory_mib=0.,peak_uss_mib=None))
            row['peak_memory_mib']=max(row['peak_memory_mib'],sum(p['rss'] for p in group)/1024**2)
            if all(p.get('uss') is not None for p in group): row['peak_uss_mib']=max(row['peak_uss_mib'] or 0,sum(p['uss'] for p in group)/1024**2)
    # Preserve PID identity when joining CPU deltas to semantic roles.
    for p in physical:
        if p['scope']!='process' or p['sampled_cpu_seconds'] is None: continue
        # A PID's registry role is stable for this captured operation. Historical
        # captures without identities remain explicitly unclassified.
        names={v for k,v in labels.items() if k[0]==p['pid']}
        if len(names)!=1: continue
        row=roles[next(iter(names))]
        row['sampled_cpu_seconds']=(row['sampled_cpu_seconds'] or 0)+p['sampled_cpu_seconds']
    return list(roles.values())
