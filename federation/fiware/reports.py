"""Portable CSV/JSON and publication-ready plot exports for measured runs."""
import csv
import io
import json
import math
from pathlib import Path
import re
import zipfile
import hashlib
from ..metrics import distribution
from .latency_metrics import PATHS, metric_info


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def process_capture(folder):
    path=Path(folder)/'operation-resources.json'
    return path if path.exists() else Path(folder)/'resources.json'


def bytes_value(value):
    match=re.fullmatch(r'\s*([\d.]+)\s*([a-zA-Z]*)\s*',str(value))
    if not match: return None
    scales={'':1,'B':1,'kB':1e3,'KB':1e3,'MB':1e6,'GB':1e9,'TB':1e12,
            'KiB':1024,'MiB':1024**2,'GiB':1024**3,'TiB':1024**4}
    return float(match[1])*scales[match[2]] if match[2] in scales else None


def docker_rows(raw):
    try:
        data=json.loads(raw)
        return data if isinstance(data,list) else [data]
    except (ValueError,TypeError):
        rows=[]
        for line in (raw or '').splitlines():
            try: rows.append(json.loads(line))
            except ValueError: continue
        return rows


def resource_series(folder):
    folder=Path(folder)
    payload=read_json(process_capture(folder),{})
    samples=payload.get('samples',[])
    first=samples[0]['wall_ns'] if samples else None
    series=[];previous={};previous_time=None
    for sample in samples:
        now=sample['wall_ns'];cpu=0.;cores=0.;read=0.;write=0.;matched=0;rates=0
        for process in sample['processes']:
            identity=(process['pid'],process.get('create_time'))
            last=previous.get(identity)
            if last:
                matched+=1
                delta=max(0,process['cpu_seconds']-last['cpu_seconds'])
                elapsed=(process.get('sampled_ns',now)-last['_sampled_ns'])/1e9
                cpu+=delta
                if elapsed>0: cores+=delta/elapsed;rates+=1
                read+=max(0,process.get('read_bytes',0)-last.get('read_bytes',0))
                write+=max(0,process.get('write_bytes',0)-last.get('write_bytes',0))
            previous[identity]=dict(process,_sampled_ns=process.get('sampled_ns',now))
        interval=(now-previous_time)/1e9 if previous_time is not None else None
        uss=[p.get('uss') for p in sample['processes']]
        series.append(dict(scope='application',elapsed_seconds=(now-first)/1e9,
            memory_mib=sum(p['rss'] for p in sample['processes'])/1024**2,
            rss_mib=sum(p['rss'] for p in sample['processes'])/1024**2,
            uss_mib=sum(uss)/1024**2 if uss and all(v is not None for v in uss) else None,
            cpu_percent=100*cores if rates else None,
            cpu_cores=cores if rates else None,cpu_seconds_delta=cpu if matched else None,
            read_bytes=read if interval else None,write_bytes=write if interval else None,
            process_count=len(sample['processes'])))
        previous_time=now
    for sample in read_json(folder/'fiware-resources.json',{}).get('samples',[]):
        containers=docker_rows(sample.get('docker_stats'))
        if sample.get('returncode',0)!=0 or not containers: continue
        memory=[];cpu=[];network_rx=[];network_tx=[];block_read=[];block_write=[]
        for container in containers:
            usage=container.get('MemUsage',container.get('MemUsageBytes'))
            mem=bytes_value(str(usage).split('/')[0])
            try: percent=float(str(container.get('CPUPerc',container.get('CPUPercent',''))).strip('%'))
            except ValueError: percent=None
            if mem is not None: memory.append(mem)
            if percent is not None: cpu.append(percent)
            for field,left,right in [('NetIO',network_rx,network_tx),('BlockIO',block_read,block_write)]:
                values=str(container.get(field,'')).split('/')
                if len(values)==2:
                    a,b=map(bytes_value,values)
                    if a is not None: left.append(a)
                    if b is not None: right.append(b)
        if first is None: first=sample['wall_ns']
        series.append(dict(scope='fiware',elapsed_seconds=(sample['wall_ns']-first)/1e9,
            memory_mib=sum(memory)/1024**2 if len(memory)==len(containers) else None,rss_mib=None,
            uss_mib=None,cpu_percent=sum(cpu) if len(cpu)==len(containers) else None,
            cpu_cores=sum(cpu)/100 if len(cpu)==len(containers) else None,
            container_count=len(containers),
            network_rx_bytes=sum(network_rx) if len(network_rx)==len(containers) else None,
            network_tx_bytes=sum(network_tx) if len(network_tx)==len(containers) else None,
            block_read_bytes=sum(block_read) if len(block_read)==len(containers) else None,
            block_write_bytes=sum(block_write) if len(block_write)==len(containers) else None))
    return sorted(series,key=lambda r:r['elapsed_seconds'])


def latency_series(folder):
    folder=Path(folder);rows=[]
    for trace in sorted((folder/'fiware-control').glob('*.jsonl')):
        for line in trace.read_text().splitlines():
            event=json.loads(line)
            if 'value_ms' in event:
                rows.append(dict(metric=event['metric'],value_ms=event['value_ms'],taz=event.get('taz'),seq=event.get('seq')))
    trace=folder/'ingestion.jsonl'
    events=(json.loads(line) for line in trace.read_text().splitlines()) if trace.exists() else read_json(folder/'ingestion.json',[])
    for event in events:
        for name in ['iot_accept_ms','schedule_lateness_ms']:
            if event.get(name) is not None:
                rows.append(dict(metric=name,value_ms=event[name],taz=event.get('taz'),attempt=event.get('attempt',1)))
    for period in read_json(folder/'manifest.json',{}).get('hourly_verification',[]):
        value=period.get('persistence_verify_ms')
        if value is not None: rows.append(dict(metric='hour_persistence_verify_ms',value_ms=value))
    cycles=folder/'cycles.jsonl'
    if cycles.exists():
        taz_ids=read_json(folder/'summary.json',{}).get('taz_ids',[])
        for line in cycles.read_text().splitlines():
            event=json.loads(line)
            for name in ['coordinator_ms','decision_barrier_ms','control_to_next_observation_ms','simulation_ms','cycle_ms']:
                if name in event: rows.append(dict(metric=name,value_ms=event[name],seq=event['seq']))
            for i,value in enumerate(event.get('local_inference_ms',[])):
                rows.append(dict(metric='local_inference_ms',value_ms=value,seq=event['seq'],taz=taz_ids[i] if i<len(taz_ids) else None))
    for event in read_json(folder/'world/tls-feedback.json',[]):
        if 'actuator_readback_ms' in event: rows.append(dict(metric='actuator_readback_ms',value_ms=event['actuator_readback_ms'],tls=event['tls']))
    return rows


def write_csv(path,rows,columns=None):
    columns=columns or list(dict.fromkeys(k for row in rows for k in row)) or ['mode','kind']
    with path.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=columns);writer.writeheader();writer.writerows(rows)


def export_report(folder, mode=None, kind=None):
    folder=Path(folder).resolve()
    manifest=read_json(folder/'manifest.json',{})
    run=read_json(folder/'scenario-run.json',{})
    summary=read_json(folder/'summary.json',{})
    config=summary.get('config',{})
    control_run=read_json(folder/'control-run.json',{})
    network_hash=manifest.get('network_sha256') or run.get('network_sha256') or summary.get('network_sha256')
    network_source='recorded metadata'
    if network_hash is None:
        for capture in [folder/'world/static/full.net.xml',folder/'static/full.net.xml']:
            if capture.exists():
                network_hash=hashlib.sha256(capture.read_bytes()).hexdigest()
                network_source='captured simulation network';break
    mode=mode or manifest.get('mode') or run.get('mode') or config.get('mode') or summary.get('mode')
    if mode not in ('monolithic','federated'): raise ValueError('Run architecture must be known for report export')
    kind=kind or ('emulation' if manifest else 'simulation')
    metadata=dict(mode=mode,kind=kind,run=folder.name,
        status=manifest.get('status',summary.get('status','failed' if (folder/'failure.json').exists() else 'unknown')),
        control=run.get('control',config.get('control',summary.get('control'))),
        profile=config.get('profile'),completed_cycles=summary.get('completed_cycles'),
        seed=run.get('seed',config.get('seed')),
        simulated_end_seconds=summary.get('simulated_end_seconds'),
        gui=run.get('gui',config.get('gui',summary.get('gui',False))),
        gui_delay=run.get('gui_delay',config.get('gui_delay',summary.get('gui_delay'))),
        replicas=run.get('replicas',config.get('replica',False)),
        integrated=run.get('integrated',bool(config.get('fiware_tenant'))),
        control_protocol_version=run.get('control_protocol_version',control_run.get('control_protocol_version',1 if run.get('integrated') or config.get('fiware_tenant') else 0)),
        grafana_probe=control_run.get('grafana_probe'),
        fiware_tenant=run.get('fiware_tenant',config.get('fiware_tenant')),
        step_seconds=run.get('step_seconds',config.get('step_seconds')),
        source_start=run.get('source_start',config.get('fiware_start')),
        operation_wall_seconds=run.get('operation_wall_seconds',summary.get('wall_seconds')),
        pacing=manifest.get('pacing'),scenario=run.get('scenario'),
        source_periods=manifest.get('periods') or [dict(day=manifest.get('day'),hour=h) for h in manifest.get('hours',[])],
        source_sha256=manifest.get('source_sha256'),network_sha256=network_hash,network_hash_source=network_source,
        routes_sha256=run.get('routes_sha256',summary.get('routes_sha256')),
        collector_errors=[s.get('error') for s in read_json(folder/'fiware-resources.json',{}).get('samples',[]) if s.get('error')],
        dashboard_included=any(p.get('role')=='dashboard' for sample in read_json(process_capture(folder),{}).get('samples',[]) for p in sample['processes']),
        notes=['Application RSS and container memory are separate scopes; do not add them blindly.',
            'Plots use CPU cores (1 = one fully occupied logical CPU). Raw cpu_percent retains the 100%-per-core convention; sampled estimates can miss short processes.',
            'Source playback time is included in pacing lateness, not in IoT HTTP latency.',
            'Plot generation is performed after resource capture.'])
    if (folder/'failure.json').exists():metadata['status']='failed'
    metadata['notes']+=['FIWARE publish-to-read timings include HTTP, broker processing and polling, not pure network propagation.',
        'Docker network totals count container interfaces, including both endpoints of internal transfers.',
        'Whole-operation process capture includes startup, provisioning, physics, control and teardown when available.']
    resources=[dict(mode=mode,kind=kind,**row) for row in resource_series(folder)]
    latencies=[dict(mode=mode,kind=kind,**row) for row in latency_series(folder)]
    grouped={}
    for row in latencies: grouped.setdefault(row['metric'],[]).append(row['value_ms'])
    out=folder/'metrics';out.mkdir(exist_ok=True)
    write_csv(out/'resources.csv',resources);write_csv(out/'latencies.csv',latencies)
    metadata['latency_ms']={name:distribution(values) for name,values in grouped.items()}
    write_csv(out/'latency-guide.csv',[metric_info(name) for name in grouped])
    probe_errors=[]
    for path in (folder/'fiware-control').glob('*.jsonl'):
        for line in path.read_text().splitlines():
            event=json.loads(line)
            if event.get('metric')=='grafana_probe_unavailable': probe_errors.append(event)
    metadata['grafana_probe_failures']=probe_errors
    metadata['resource_peaks']={}
    for scope in ['application','fiware']:
        group=[r for r in resources if r['scope']==scope]
        peaks={}
        for name in ['memory_mib','rss_mib','uss_mib','cpu_percent','cpu_cores']:
            values=[r[name] for r in group if r.get(name) is not None]
            peaks[name]=max(values) if values else None
        metadata['resource_peaks'][scope]=peaks
    metadata['resource_sample_count']=len(resources)
    publications=[]
    for path in (folder/'fiware-control').glob('*.jsonl'):
        publications.extend(r for line in path.read_text().splitlines() if (r:=json.loads(line)).get('publication'))
    metadata['control_transport']=dict(logical_events=len({r['event_id'] for r in publications}),
        publication_attempts=len(publications),encoded_http_body_bytes=sum(r.get('payload_bytes',0) for r in publications),
        note='HTTP JSON body bytes include retries, but exclude HTTP headers, TCP/IP and responses.') if publications else None
    metadata['resource_totals']={}
    for scope in ['application','fiware']:
        group=sorted([r for r in resources if r['scope']==scope],key=lambda r:r['elapsed_seconds'])
        cpu=0.
        for previous,current in zip(group,group[1:]):
            if current.get('cpu_percent') is not None: cpu+=current['cpu_percent']/100*(current['elapsed_seconds']-previous['elapsed_seconds'])
        cpu_available=len(group)>1
        if scope=='application':
            deltas=[r['cpu_seconds_delta'] for r in group if r.get('cpu_seconds_delta') is not None]
            cpu=sum(deltas);cpu_available=bool(deltas)
        totals=dict(sampled_cpu_seconds=cpu if cpu_available else None,
            sampled_wall_seconds=group[-1]['elapsed_seconds']-group[0]['elapsed_seconds'] if len(group)>1 else None)
        if scope=='application':
            for k in ['read_bytes','write_bytes']: totals[k]=sum(r.get(k) or 0 for r in group) if group else None
        else:
            for k in ['network_rx_bytes','network_tx_bytes','block_read_bytes','block_write_bytes']:
                vals=[r[k] for r in group if r.get(k) is not None]
                totals[k]=max(0,vals[-1]-vals[0]) if len(vals)>1 else None
        metadata['resource_totals'][scope]=totals
    cpu_totals=[v['sampled_cpu_seconds'] for v in metadata['resource_totals'].values()]
    metadata['accounted_cpu_seconds']=sum(cpu_totals) if all(v is not None for v in cpu_totals) else None
    metadata['notes'].append('Accounted CPU sums disjoint application/container scopes; Docker daemon, kernel and unrelated processes are excluded. Container CPU seconds are sampled estimates.')
    (out/'summary.json').write_text(json.dumps(metadata,indent=2))
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    coverage=[]
    def finish(fig,name,available,reason):
        if available:
            for suffix in ['png','svg']:fig.savefig(out/f'{name}.{suffix}',dpi=160)
        else:
            for suffix in ['png','svg']:(out/f'{name}.{suffix}').unlink(missing_ok=True)
            coverage.append(dict(plot=name,reason=reason))
        plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(9,6),sharex=True)
    for scope,label in [('application','Application + SUMO'),('fiware','FIWARE containers')]:
        rows=[r for r in resources if r['scope']==scope]
        if rows:
            axes[0].plot([r['elapsed_seconds'] for r in rows],[r.get('memory_mib') for r in rows],label=label)
            axes[1].plot([r['elapsed_seconds'] for r in rows],[r.get('cpu_cores') for r in rows],label=label)
    axes[0].set_ylabel('Memory (MiB)');axes[1].set_ylabel('CPU cores used');axes[1].set_xlabel('Wall time since first sample (s)')
    fig.suptitle(f'{mode.title()} · {kind} · {folder.name}')
    for ax in axes:
        ax.grid(alpha=.2)
        if ax.lines: ax.legend()
    fig.tight_layout()
    finish(fig,'footprint',bool(resources),'No resource samples')
    fig,ax=plt.subplots(figsize=(9,max(3,len(grouped)*.4)))
    names=[n for n in [*PATHS[:2],'control_closed_loop_ms','cycle_ms','iot_accept_ms','schedule_lateness_ms','hour_persistence_verify_ms'] if n in grouped]
    fig.set_size_inches(10,max(3,len(names)*.6))
    if names:
        ax.barh([metric_info(n)['label'] for n in names],[metadata['latency_ms'][n]['p95'] for n in names])
        # Inference/readback can be microseconds while physics takes seconds.
        # Keep zero representable and small transmission bars readable.
        ax.set_xscale('symlog',linthresh=.1);ax.set_xlim(left=0)
        ax.set_xlabel('p95 latency (ms; logarithmic above 0.1 ms)')
    else: ax.text(.5,.5,'No control/ingestion latency samples for this run',ha='center',transform=ax.transAxes)
    ax.set_title(f'{mode.title()} · {kind} · latency');fig.tight_layout()
    finish(fig,'latency',bool(names),'No primary latency samples')
    present=[name for name in PATHS[:2] if metadata['latency_ms'].get(name)]
    if present:
        fig,axes=plt.subplots(len(present),1,figsize=(10,3*len(present)),squeeze=False)
        for ax,name in zip(axes.flat,present):
            values=metadata['latency_ms'][name];numbers=[values['p50'],values['p95']]
            bars=ax.barh(['Median','p95'],numbers,color=['#2878b5','#f28e2b'])
            ax.bar_label(bars,labels=[f'{value:.2f} ms' for value in numbers],padding=4)
            ax.set_xlim(right=max(numbers)*1.2 if max(numbers)>0 else 1)
            ax.set_xlabel('Real milliseconds');ax.set_title(metric_info(name)['label'])
        fig.suptitle(f'{mode.title()} · observed paths (Grafana query, not browser rendering)');fig.tight_layout()
        finish(fig,'data-path-latency',True,'')
    else:
        for suffix in ['png','svg']:(out/f'data-path-latency.{suffix}').unlink(missing_ok=True)
    for name in PATHS[:2]:
        if name not in present: coverage.append(dict(plot=metric_info(name)['label'],reason='Not applicable: no agent controller' if name==PATHS[1] and metadata.get('control')=='none' else 'Not recorded in this run; no historical reconstruction is possible'))
    from .breakdown import component_costs,policy_work,role_costs
    components=component_costs(folder);work=policy_work(folder);roles=role_costs(folder)
    if any(r['component']=='Other runtime support (role not recorded)' for r in roles):
        coverage.append(dict(plot='Process roles',reason='Some captured PIDs have no role metadata; grouped as runtime support, never guessed to be agents'))
    write_csv(out/'components.csv',components);write_csv(out/'policy-work.csv',work);write_csv(out/'roles.csv',roles)
    fields=[(f,label) for f,label in [('mean_inference_wall_ms','Wall ms per decision'),('mean_inference_cpu_ms','CPU ms per decision')] if any(r.get(f) is not None for r in work)]
    if fields:
        fig,axes=plt.subplots(len(fields),1,figsize=(10,3*len(fields)),squeeze=False)
        for ax,(field,label) in zip(axes.flat,fields):
            rows=[r for r in work if r.get(field) is not None]
            ax.bar([r['taz'] for r in rows],[r[field] for r in rows]);ax.set_ylabel(label);ax.set_xlabel('TAZ controller')
        fig.suptitle(f'{mode.title()} · measured local policy work');fig.tight_layout()
        finish(fig,'policy-footprint',True,'')
    else:
        for suffix in ['png','svg']:(out/f'policy-footprint.{suffix}').unlink(missing_ok=True)
    if not any(r.get('mean_inference_cpu_ms') is not None for r in work):
        coverage.append(dict(plot='Policy CPU',reason='No policy decisions' if not work else 'CPU-per-decision instrumentation was absent; wall time is still available'))
    containers=[r for r in components if r.get('service')=='iot']
    fig,axes=plt.subplots(1,2,figsize=(12,max(3,len(containers)*.4)))
    for ax,field,label in zip(axes,['sampled_cpu_seconds','peak_memory_mib'],['CPU seconds (sampled)','Peak Docker memory (MiB)']):
        rows=[r for r in containers if r.get(field) is not None]
        labels=['City IoT Agent' if mode=='monolithic' else 'TAZ '+r['component'].split('-')[-3].upper()+' IoT Agent' for r in rows]
        ax.barh(labels,[r[field] for r in rows]);ax.set_xlabel(label)
    fig.suptitle(f'{mode.title()} · FIWARE IoT Agents');fig.tight_layout()
    finish(fig,'iot-agent-footprint',bool(containers),'No container samples')
    fields=[(field,label) for field,label in [('sampled_cpu_seconds','CPU seconds (sampled)'),('peak_memory_mib','Peak RSS (MiB)')] if any(r.get(field) is not None for r in roles)]
    if fields:
        fig,axes=plt.subplots(1,len(fields),figsize=(6*len(fields),max(3,len(roles)*.4)),squeeze=False)
        for ax,(field,label) in zip(axes.flat,fields):
            rows=[r for r in roles if r.get(field) is not None]
            ax.barh([r['component'] for r in rows],[r[field] for r in rows]);ax.set_xlabel(label)
        fig.suptitle(f'{mode.title()} · resources by process role');fig.tight_layout()
        finish(fig,'process-footprint',True,'')
    else:
        for suffix in ['png','svg']:(out/f'process-footprint.{suffix}').unlink(missing_ok=True)
    write_csv(out/'coverage.csv',coverage,['plot','reason'])
    metadata['coverage']=coverage
    (out/'summary.json').write_text(json.dumps(metadata,indent=2))
    with zipfile.ZipFile(out/'metrics-export.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for file in out.iterdir():
            if file.name!='metrics-export.zip': archive.write(file,file.name)
        for name in ['process-components.json','operation-resources.json','resources.json','fiware-resources.json','ingestion.jsonl','ingestion.json','cycles.jsonl','world/tls-feedback.json','scenario-run.json','control-run.json','failure.json','environment.json']:
            if (folder/name).exists(): archive.write(folder/name,'raw/'+name)
        for path in (folder/'fiware-control').glob('*.jsonl'): archive.write(path,'raw/fiware-control/'+path.name)
    return out
