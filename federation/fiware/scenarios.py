"""Fit SUMO routes to verified FIWARE history on the canonical GreenWave map."""
from collections import defaultdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from ..models import ROOT, VENDOR
from ..metrics import ResourceMonitor
from .emulator import save
from .history import read_session
from .observations import digest


def training_demand(profile, day, hour, noise=1., enforce_training_hours=True):
    config=json.loads((ROOT/'configs/training-profiles.json').read_text())[profile]
    settings=config['experiment_settings']
    weekend=datetime.fromisoformat(day).weekday()>=5
    if enforce_training_hours and ((settings['day_filter']=='weekdays' and weekend) or (settings['day_filter']=='weekends' and not weekend)):
        raise ValueError('The selected profile was trained on a different day type')
    if enforce_training_hours and hour not in settings['focus_hours']: raise ValueError('Hour is outside this profile\'s training hours')
    if not math.isfinite(noise) or noise<=0: raise ValueError('Demand noise must be finite and positive')
    demand=config['demand_settings']
    hourly=demand.get('weekend_hourly_profile') if weekend else None
    hourly=hourly or demand['hourly_profile']
    factor=demand['weekend_demand_factor'] if weekend else 1.
    return max(1,int(round(demand['base_demand']*float(hourly[str(hour)])*factor*noise)))


def sumo_tool(name):
    roots=[Path(os.environ.get('SUMO_TOOLS','/usr/share/sumo/tools'))]
    if os.environ.get('SUMO_HOME'): roots.insert(0,Path(os.environ['SUMO_HOME'])/'tools')
    for root in roots:
        if (root/name).is_file(): return str(root/name)
    raise FileNotFoundError(f'SUMO tool not found: {name}; set SUMO_HOME or SUMO_TOOLS')


def write_edge_counts(rows, path):
    """Counts are per detector edge, not an estimate of total vehicles in the city."""
    if not rows: raise ValueError('No persisted traffic measurements')
    known={e.get('id') for e in ET.parse(VENDOR/'sumoenv/static/full.net.xml').getroot().findall('edge') if not e.get('id','').startswith(':')}
    counts=defaultdict(list)
    for row in rows:
        if row['edge_id'] not in known: raise ValueError('Persisted detector edge does not exist in the GreenWave map')
        counts[row['edge_id']].append(row['traffic_flow'])
    root=ET.Element('data')
    interval=ET.SubElement(root,'interval',begin='0',end='3600')
    # Multiple sensors on one edge can observe the same vehicles. Use their mean,
    # explicitly recorded in scenario metadata, rather than adding repeated counts.
    for edge, values in sorted(counts.items()):
        ET.SubElement(interval,'edge',id=edge,entered=str(round(sum(values)/len(values))))
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
    return dict(edges=len(counts),measurements=len(rows),duplicate_edge_rule='rounded_mean')


def route_count(path):
    root=ET.parse(path).getroot()
    return len(root.findall('vehicle'))+sum(int(e.get('number','0')) for e in root.findall('flow'))


def build_scenario(manifest_path, hour, profile, output, noise=1., seed=42,
                   candidates=None, history_reader=read_session, day=None):
    manifest=json.loads(Path(manifest_path).read_text())
    if manifest['status']!='verified': raise ValueError('Replay is not verified; scenario generation is disabled')
    canonical=digest(VENDOR/'sumoenv/static/full.net.xml')
    if canonical!=manifest['network_sha256']: raise ValueError('Map changed since emulation')
    if not 0 <= hour <= 23: raise ValueError('Hour must be in 0..23')
    selected_day=day or manifest['day']
    total=training_demand(profile,selected_day,hour,noise,enforce_training_hours=False)
    # The only counts passed to routeSampler are read back from FIWARE storage.
    rows=history_reader(manifest,selected_day,hour)
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=False)
    metadata=dict(status='building',manifest=str(Path(manifest_path).resolve()),
        replay_tenant=manifest['tenant'],source_mode=manifest['mode'],day=selected_day,hour=hour,
        profile=profile,seed=seed,demand_noise=noise,vehicle_count=total,network_sha256=canonical,
        counts_source='QuantumLeap Timescale history')
    save(output/'scenario.json',metadata)
    try:
        metadata['edge_counts']=write_edge_counts(rows,output/'edgedata.xml')
        save(output/'observations.json',rows)
        candidate_path=output/'candidates.rou.xml'
        if candidates:
            shutil.copyfile(candidates,candidate_path)
        else:
            subprocess.run([sys.executable,sumo_tool('randomTrips.py'),'-n',str(VENDOR/'sumoenv/static/full.net.xml'),
                '-r',str(candidate_path),'--output-trip-file',str(output/'trips.xml'),
                '--random-departpos','--random-arrivalpos','--allow-fringe','--remove-loops',
                '--fringe-factor','10','--min-distance','500','--max-distance','3000',
                '--random-routing-factor','10','--period','0.1','--seed',str(seed)],check=True)
        subprocess.run([sys.executable,sumo_tool('routeSampler.py'),'-r',str(candidate_path),
            '--edgedata-files',str(output/'edgedata.xml'),'--edgedata-attribute','entered',
            '-o',str(output/'routes.rou.xml'),'--write-flows','number','--total-count',str(total),
            '--optimize','full','--minimize-vehicles','1','--threads','1','--seed',str(seed),
            '--mismatch-output',str(output/'mismatch.xml')],check=True)
        actual=route_count(output/'routes.rou.xml')
        if actual<=0: raise ValueError('Route fitting generated no vehicles')
        metadata.update(status='ready',actual_route_vehicle_count=actual,routes_sha256=digest(output/'routes.rou.xml'))
        save(output/'scenario.json',metadata)
        print(f'Scenario ready: {output}',flush=True)
        return output
    except BaseException as error:
        metadata.update(status='failed',error=str(error));save(output/'scenario.json',metadata)
        raise


def validate_scenario(path):
    path=Path(path).resolve()
    metadata=json.loads((path/'scenario.json').read_text())
    if metadata['status']!='ready': raise ValueError('Scenario is not ready')
    if metadata['network_sha256']!=digest(VENDOR/'sumoenv/static/full.net.xml'): raise ValueError('Scenario map differs from the canonical map')
    if metadata['routes_sha256']!=digest(path/'routes.rou.xml'): raise ValueError('Scenario routes changed after preparation')
    return path,metadata


def _run_scenario(path, mode, control, output, replicas=False, gui=False, gui_delay=20, fiware_spec=None):
    from .deployment import validate_mode
    validate_mode(mode)
    path,metadata=validate_scenario(path)
    if gui_delay<0: raise ValueError('GUI delay must be nonnegative')
    if control not in ('none','local','coordinated'): raise ValueError('Unknown control mode')
    if replicas and (mode!='federated' or control=='none'):
        raise ValueError('Replicas require a federated RL run')
    output=Path(output).resolve()
    if output.exists(): raise FileExistsError('Choose a new simulation output directory')
    if control!='none':
        training_demand(metadata['profile'],metadata['day'],metadata['hour'],metadata['demand_noise'])
        command=[sys.executable,'-m','federation','--mode',mode,'--control',control,
            '--profile',metadata['profile'],'--routes',str(path/'routes.rou.xml'),
            '--hour',str(metadata['hour']),'--vehicle-count',str(metadata['vehicle_count']),
            '--demand-noise',str(metadata['demand_noise']),'--seed',str(metadata['seed']),
            '--steps','6','--step-seconds','600','--end','3600','--output',str(output)]
        if replicas: command.append('--replica')
        if gui: command.extend(['--gui','--gui-delay',str(gui_delay)])
        if fiware_spec: command.extend(['--fiware-tenant',fiware_spec['fiware_tenant'],'--fiware-start',fiware_spec['fiware_start']])
        subprocess.run(command,cwd=ROOT,check=True)
        return
    # A true no-agent baseline uses SUMO's normal TLS programs; no model is loaded.
    output.mkdir(parents=True)
    shutil.copytree(VENDOR/'sumoenv/static',output/'static')
    (output/'output').mkdir()
    config=ET.Element('configuration')
    inputs=ET.SubElement(config,'input')
    for tag,value in [('net-file',output/'static/full.net.xml'),('route-files',path/'routes.rou.xml'),
                      ('additional-files',output/'static/e2Detector.xml')]:
        ET.SubElement(inputs,tag,value=str(value))
    timing=ET.SubElement(config,'time');ET.SubElement(timing,'end',value='3600')
    reporting=ET.SubElement(config,'output')
    for tag,name in [('tripinfo-output','tripinfos.xml'),('emission-output','emission-output.xml'),('summary-output','sumo-summary.xml')]:
        ET.SubElement(reporting,tag,value=str(output/'output'/name))
    processing=ET.SubElement(config,'processing')
    ET.SubElement(processing,'time-to-teleport',value='-1')
    ET.SubElement(processing,'ignore-junction-blocker',value='1')
    ET.SubElement(processing,'waiting-time-memory',value='3600')
    ET.ElementTree(config).write(output/'run.sumocfg',encoding='utf-8',xml_declaration=True)
    resources=ResourceMonitor(output/'resources.json')
    start=time.perf_counter()
    try:
        command=[('sumo-gui' if gui else 'sumo'),*(['--start','--quit-on-end','--delay',str(gui_delay)] if gui else []),'-c',str(output/'run.sumocfg'),'--seed',str(metadata['seed']),
            '--no-step-log','true','--duration-log.disable','true','--log',str(output/'sumo.log')]
        extra={}
        if fiware_spec:
            from .passive import run_passive
            extra=run_passive(command,fiware_spec)
        else: subprocess.run(command,check=True)
        save(output/'summary.json',dict(status='completed',control='none',mode=mode,gui=gui,gui_delay=gui_delay if gui else 0,scenario=str(path),
            simulated_end_seconds=3600,wall_seconds=time.perf_counter()-start,
            routes_sha256=metadata['routes_sha256'],network_sha256=metadata['network_sha256'],**{k:v for k,v in extra.items() if k!='wall_seconds' and k!='simulated_end_seconds'}))
    except BaseException as error:
        save(output/'failure.json',dict(status='failed',error=str(error)));raise
    finally: resources.close()


def run_scenario(path, mode, control, output, replicas=False, gui=False, gui_delay=20, integrated=True):
    from .container_metrics import monitor_containers
    # Do not let sampling create an output directory before the runtime owns it.
    if Path(output).exists(): raise FileExistsError('Choose a new simulation output directory')
    path,metadata=validate_scenario(path)
    from .observations import time_window
    import uuid
    spec=dict(mode=mode,output=str(Path(output).resolve()),fiware_tenant='gw_sim_'+uuid.uuid4().hex[:24],
        fiware_start=time_window(metadata['day'],metadata['hour'])[0],step_seconds=600,timeout=120) if integrated else None
    measured=ResourceMonitor(Path(output)/'operation-resources.json')
    started=time.perf_counter()
    try:
        with monitor_containers(mode,output):
            _run_scenario(path,mode,control,output,replicas,gui,gui_delay,fiware_spec=spec)
    except BaseException as error:
        Path(output).mkdir(parents=True,exist_ok=True)
        failure_path=Path(output)/'failure.json'
        failure=json.loads(failure_path.read_text()) if failure_path.exists() else dict(status='failed',error=str(error))
        failure['launcher_error']=str(error)
        save(failure_path,failure)
        raise
    finally:
        Path(output).mkdir(parents=True,exist_ok=True)
        measured.close()
        save(Path(output)/'scenario-run.json',dict(scenario=str(Path(path).resolve()),
            network_sha256=metadata['network_sha256'],routes_sha256=metadata['routes_sha256'],profile=metadata['profile'],
            seed=metadata['seed'],
            mode=mode,control=control,replicas=replicas,gui=gui,gui_delay=gui_delay if gui else 0,
            integrated=integrated,fiware_tenant=spec['fiware_tenant'] if spec else None,
            control_protocol_version=2 if integrated else 0,
            step_seconds=600,source_start=spec['fiware_start'] if spec else None,
            operation_wall_seconds=time.perf_counter()-started))
        from .reports import export_report
        try: export_report(output,mode,'simulation')
        except Exception as error: save(Path(output)/'metrics-error.json',dict(error=str(error)))
