"""Two focused control rooms; operational controls and diagnostics are separated."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import uuid
from urllib.parse import urlencode
import streamlit as st
from ..models import ROOT
from .client import FiwareClient
from .deployment import TAZS, endpoints, generate, compose_command, service_key
from .jobs import start_job, list_jobs, stop_job, tail_log
from .observations import time_window
from .timing import read_period
from .scenarios import training_demand
from .reports import export_report
from .workspace import service_status, saved_scenarios, wipe_scenarios
from .measurements import measurement_catalog, select_measurements, measurement_label
from .latency_metrics import PATHS, GROUPS, metric_info
from .comparison import compatibility, export_comparison


@st.cache_data(ttl=10)
def cached_service_status(mode):
    return service_status(mode)


@st.cache_data
def catalog(path, modified):
    import csv
    with Path(path).open(newline='',encoding='utf-8-sig') as handle:
        return sorted({row['data'] for row in csv.DictReader(handle,delimiter=';')})


@st.cache_data
def preview_period(path, modified, start, duration):
    observations=read_period(path,start,duration)
    return [o.to_dict() for o in observations]


def document_paths(root, filename, predicate=lambda _:True):
    found=[]
    for path in sorted(root.glob('**/'+filename),reverse=True):
        try:
            data=json.loads(path.read_text())
            if predicate(data): found.append((path,data))
        except (OSError,json.JSONDecodeError): continue
    return found


def launch(mode, kind, arguments):
    try:
        identifier=start_job(mode,kind,arguments)
        st.success('Operation started. Progress is shown below.')
        return identifier
    except Exception as error: st.error(str(error))


def new_path(category, mode):
    return ROOT/category/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-')+mode+'-'+uuid.uuid4().hex[:6])


def periods(manifest):
    return manifest.get('periods') or [dict(day=manifest['day'],hour=h) for h in manifest['hours']]


def footprint_tab(mode):
    import pandas as pd
    st.subheader('Footprint & latency')
    with st.expander('How to compare monolithic and federated',expanded=False):
        st.markdown('''**Run one architecture at a time.** Stop the other deployment with **Stop services**. Keep the same dashboard launch method for every run.

**1. Compare FIWARE ingestion:** in each dashboard, replay the same dataset, start time, duration, speed, reporting interval, transmission delay and seed. Wait for each replay to be verified. Choose Data emulation under Measurements to evaluate. Compare FIWARE CPU/memory, IoT acceptance latency, source schedule lateness and persistence verification time.

**2. Compare the full simulation/control pipeline:** prepare one scenario and select that exact saved scenario in both dashboards. Start the selected architecture's services. Keep FIWARE control enabled, use the same controller (for example, Local agents + coordinator), keep SUMO GUI off and additional replicas off. Run sequentially. Compare full-operation resources and observation/coordination/command/feedback transmission, inference, actuation, persistence and cycle latency. The shared control interval is 600 simulated seconds.

**3. Inspect results here:** select both architectures and the matching measured operations below. Resource scope switches between application/SUMO and FIWARE containers. Download each ZIP to retain raw measurements and plots. Repeat paired runs at least five times as a starting point; compare distributions across runs.

The no-agent simulation publishes telemetry and TLS state through FIWARE without policy commands. Its snapshot schema differs from RL observations; compare no-agent with no-agent. Offline mode bypasses FIWARE and must be compared separately. Additional full-city replicas are a separate experiment. These dashboards compare the new common-map monolith with the federated implementation; they do not automatically benchmark the original BoMoDT checkout.''')
    st.caption('Reports retain architecture, operation, controller, GUI and pacing settings. Compare matching workloads; GUI and accelerated playback affect measurements.')
    category=st.radio('Measurements to evaluate',['Simulations','Data emulation'],horizontal=True)
    kind='simulation' if category=='Simulations' else 'emulation'
    records=measurement_catalog(ROOT,kind)
    choices=st.multiselect('Architectures to compare',['monolithic','federated'],default=[mode])
    with st.expander('Other recorded operations'):
        include_cli=st.checkbox('Include CLI and validation runs',value=False)
        include_failed=st.checkbox('Include failed or incomplete operations',value=False)
        include_offline=st.checkbox('Include offline simulation baselines',value=False) if kind=='simulation' else False
        st.caption('Default: completed dashboard runs with active FIWARE. Historical validation records are kept on disk and hidden here.')
    visible=select_measurements(records,choices,include_cli,include_failed,include_offline)
    options={str(r['folder']):r for r in visible}
    if not options:
        st.info('No completed dashboard '+category.lower()+' match this selection. Complete a run or use Other recorded operations to inspect older/offline/CLI results.');return
    st.caption('Select completed simulations of the same scenario and controller. Each entry is one actual run; report files are not separate operations.' if kind=='simulation' else 'Select source emulations with matching playback settings.')
    selected=st.multiselect('Simulations to compare' if kind=='simulation' else 'Emulations to compare',list(options),
        default=[next(iter(options))],format_func=lambda key:measurement_label(options[key]),key='measured-'+kind)
    if st.button('Prepare report and plots',disabled=not selected):
        with st.spinner('Preparing CSV files and plots…'):
            for i in selected:
                try: export_report(options[i]['folder'])
                except Exception as error: st.error(str(error))
    series=[];latency=[];metadata=[];reports=[];totals=[];policy_frames=[];component_frames=[]
    for i in selected:
        record=options[i]
        folder,data,architecture=record['folder'],record['data'],record['mode']
        report=folder/'metrics'
        if not (report/'summary.json').exists():
            st.info(f'{folder.name}: use Prepare report and plots for this existing run.');continue
        info=json.loads((report/'summary.json').read_text())
        reports.append(info)
        for filename,frames,required in [('policy-work.csv',policy_frames,'taz'),('components.csv',component_frames,'scope')]:
            if (report/filename).exists():
                frame=pd.read_csv(report/filename)
                if required in frame:
                    if filename=='components.csv' and (report/'roles.csv').exists():
                        roles=pd.read_csv(report/'roles.csv')
                        if 'component' in roles:
                            frame=pd.concat([frame[frame.scope!='process'],roles.assign(scope='process')],ignore_index=True)
                    frame['run']=architecture.title()+' · '+folder.name;frames.append(frame)
        metadata.append(dict(run=folder.name,architecture=architecture,scenario=record['scenario'],operation=info['kind'],
            controller=info.get('control'),integrated=info.get('integrated',False),step_seconds=info.get('step_seconds'),
            protocol_version=info.get('control_protocol_version'),grafana_probe=info.get('grafana_probe'),
            operation_wall_seconds=info.get('operation_wall_seconds'),source_periods=info.get('source_periods'),gui=info.get('gui'),pacing=info.get('pacing'),dashboard_included=info.get('dashboard_included'),status=info['status']))
        for scope,values in info.get('resource_totals',{}).items():
            totals.append(dict(run=folder.name,architecture=architecture,scope=scope,**values,**info.get('resource_peaks',{}).get(scope,{})))
        frame=pd.read_csv(report/'resources.csv')
        if 'scope' in frame:
            if 'memory_mib' not in frame: frame['memory_mib']=frame.get('rss_mib')
            frame['run']=architecture.title()+' · '+folder.name
            series.append(frame)
        for stage,values in info.get('latency_ms',{}).items():
            if values: latency.append(dict(run=folder.name,architecture=architecture,stage=stage,**values))
        archive=report/'metrics-export.zip'
        if archive.exists():
            st.download_button('Download '+folder.name,archive.read_bytes(),file_name=folder.name+'-metrics.zip',
                mime='application/zip',key='download-'+folder.name)
    if metadata:
        with st.expander('Selected run settings'):
            st.dataframe(metadata,width='stretch',hide_index=True)
    if len(reports)>1:
        fields=['kind','control','integrated','control_protocol_version','grafana_probe','step_seconds','source_start','gui','gui_delay','replicas','pacing','source_periods','source_sha256','network_sha256','routes_sha256','dashboard_included']
        different=[key for key in fields if len({json.dumps(r.get(key),sort_keys=True) for r in reports})>1]
        if different: st.warning('Selected reports differ in '+', '.join(different)+'. Check these settings before interpreting differences as architecture overhead.')
        if len({r['mode'] for r in reports})<2: st.info('Select a report from each architecture for a monolithic/federated comparison.')
    st.subheader('Presentation comparison')
    problems=compatibility(reports)
    if problems:
        st.info('To build a comparable report: '+ '; '.join(problems)+'.')
        st.caption('Choose one completed run per architecture with the same scenario, controller, GUI and FIWARE settings. A no-agent monolith and a coordinated federated run are different experiments.')
    else:
        ordered=sorted(reports,key=lambda r:r['mode']!='monolithic')
        use_uss=all(r['resource_peaks']['application'].get('uss_mib') is not None for r in ordered)
        comparison_rows=[]
        for label,unit,getter in [
            ('Application + SUMO CPU','CPU seconds',lambda r:r['resource_totals']['application'].get('sampled_cpu_seconds')),
            ('FIWARE CPU','CPU seconds (sampled)',lambda r:r['resource_totals']['fiware'].get('sampled_cpu_seconds')),
            ('Application memory peak','MiB USS' if use_uss else 'MiB RSS',lambda r:r['resource_peaks']['application'].get('uss_mib' if use_uss else 'rss_mib')),
            ('FIWARE memory peak','MiB Docker usage',lambda r:r['resource_peaks']['fiware'].get('memory_mib')),
            ('Whole run duration','seconds',lambda r:r.get('operation_wall_seconds'))]:
            values=[getter(r) for r in ordered]
            if all(v is not None for v in values):comparison_rows.append(dict(Measurement=label,Unit=unit,Monolithic=values[0],Federated=values[1]))
        st.dataframe(comparison_rows,width='stretch',hide_index=True)
        st.caption('CPU seconds measure total work. CPU cores measure parallel activity: 3200% in the old convention meant about 32 occupied cores, not 3200% of the machine. Memory scopes stay separate.')
        for name,title,definition in [
            (PATHS[0],'Observation available through Grafana','Starts when the SUMO snapshot is ready; ends at a matching Grafana query response. Browser rendering is excluded.'),
            (PATHS[1],'Stored observation to SUMO intervention','Starts at Timescale readback confirmation; ends at TLS program readback after coordinator, local inference and command delivery.')]:
            values=[r.get('latency_ms',{}).get(name) for r in ordered]
            if all(values):
                st.markdown('**'+title+'**');st.caption(definition)
                st.bar_chart(pd.DataFrame([dict(Architecture=r['mode'].title(),Statistic=label,Milliseconds=v[key]) for r,v in zip(ordered,values) for key,label in [('p50','Median'),('p95','p95')]]),x='Statistic',y='Milliseconds',color='Architecture',stack=False)
            else:st.caption(title+': not recorded in both selected runs; omitted from comparison plots.')
        if st.button('Build presentation report (PDF, plots and CSV)',type='primary'):
            try:
                destination=new_path('comparisons','paired')
                with st.spinner('Building matched comparison…'):export_comparison([options[key]['folder'] for key in selected],destination)
                st.session_state['comparison_export']=str(destination)
                st.session_state['comparison_folders']=sorted(selected)
            except Exception as error:st.error(str(error))
        if st.session_state.get('comparison_folders')==sorted(selected):
            destination=Path(st.session_state['comparison_export'])
            archive=destination/'comparison.zip'
            if archive.exists():st.download_button('Download presentation report',archive.read_bytes(),file_name=destination.name+'.zip',mime='application/zip')
        if policy_frames:
            workload=pd.concat(policy_frames,ignore_index=True)
            if workload['mean_inference_cpu_ms'].notna().all() and len(workload):
                workload['Architecture']=workload['run'].str.split(' · ').str[0]
                st.markdown('**Local controller CPU per decision**')
                st.caption('Same unit of work per TAZ; the monolith executes these controllers in its shared central process.')
                st.bar_chart(workload,x='taz',y='mean_inference_cpu_ms',color='Architecture',stack=False)
    omissions=[dict(Architecture=r['mode'].title(),Measurement=c['plot'],Reason=c['reason']) for r in reports for c in r.get('coverage',[])]
    if omissions:
        with st.expander('Data coverage — why a measurement is omitted'):
            st.dataframe(omissions,width='stretch',hide_index=True)

    with st.expander('Detailed measurements and raw per-run diagnostics',expanded=False):
        if series:
            all_rows=pd.concat(series,ignore_index=True)
            scope=st.radio('Resource scope',['application','fiware'],horizontal=True,
                format_func=lambda s:'Application and SUMO' if s=='application' else 'FIWARE containers')
            rows=all_rows[all_rows.scope==scope]
            if len(rows):
                a,b=st.columns(2)
                with a:
                    st.caption('Memory usage (MiB)')
                    st.line_chart(rows,x='elapsed_seconds',y='memory_mib',color='run')
                with b:
                    st.caption('CPU cores used (1 = one fully occupied logical CPU)')
                    if 'cpu_cores' not in rows: rows=rows.assign(cpu_cores=rows['cpu_percent']/100)
                    st.line_chart(rows,x='elapsed_seconds',y='cpu_cores',color='run')
            else: st.info('No samples for this scope. Check the recorded collector errors in Diagnostics.')
        st.subheader('Controller work per TAZ')
        st.caption('The monolith performs all logical TAZ decisions in one process with a shared policy. Federated workers have separate processes. Compare per-TAZ computation and central-runtime totals; shared monolithic RAM cannot be assigned to individual agents.')
        if policy_frames:
            frame=pd.concat(policy_frames,ignore_index=True)
            if frame['total_inference_cpu_ms'].isna().any(): st.info('Some selected runs lack CPU-per-TAZ measurements. Their CPU bars are omitted; rerun both architectures for a measured CPU comparison.')
            a,b=st.columns(2)
            with a:
                st.caption('Total policy CPU per logical controller (ms, current thread)')
                if frame['total_inference_cpu_ms'].notna().any(): st.bar_chart(frame.dropna(subset=['total_inference_cpu_ms']),x='taz',y='total_inference_cpu_ms',color='run',stack=False)
                else: st.info('CPU per TAZ was not recorded in these historical runs. A new run is required.')
            with b:
                st.caption('Total policy wall time per logical controller (ms)')
                st.bar_chart(frame,x='taz',y='total_inference_wall_ms',color='run',stack=False)
            with st.expander('Per-controller work and decision counts'): st.dataframe(frame,width='stretch',hide_index=True)
            if 'execution_component' in frame and frame['total_inference_cpu_ms'].notna().any():
                st.caption('Where local-policy CPU work executes: the central monolithic process carries all TAZ decisions; federated workers carry their own. Bars represent different decision counts, so use the per-TAZ view above for equal-work comparisons.')
                placed=frame.groupby(['run','execution_component'],as_index=False)['total_inference_cpu_ms'].sum(min_count=1)
                st.bar_chart(placed.dropna(subset=['total_inference_cpu_ms']),x='execution_component',y='total_inference_cpu_ms',color='run',stack=False)
        else: st.info('No policy work recorded. No-agent runs have no inference workload; prepare updated reports for existing agent runs.')
        if component_frames:
            with st.expander('Physical process and container costs'):
                frame=pd.concat(component_frames,ignore_index=True)
                scope=st.radio('Component type',['process','container'],horizontal=True)
                subset=frame[frame.scope==scope].copy()
                if scope=='container' and 'service' in subset:
                    services=sorted(subset['service'].dropna().unique())
                    if services:
                        service=st.selectbox('FIWARE service',services,index=services.index('iot') if 'iot' in services else 0)
                        subset=subset[subset.service==service]
                st.dataframe(subset.drop(columns=['pid'],errors='ignore'),width='stretch',hide_index=True)
                if len(subset):
                    subset['entity']=subset['component']
                    a,b=st.columns(2)
                    with a:
                        st.caption('CPU seconds per physical component (sampled)')
                        valid=subset.dropna(subset=['sampled_cpu_seconds'])
                        if len(valid): st.bar_chart(valid,x='entity',y='sampled_cpu_seconds',color='run',stack=False)
                    with b:
                        st.caption('Peak memory per physical component (MiB)')
                        st.bar_chart(subset.dropna(subset=['peak_memory_mib']),x='entity',y='peak_memory_mib',color='run',stack=False)
                st.caption('Central/global runtime includes SUMO orchestration and coordinator duties. Process RSS/USS and container memory are separate accounting scopes. Unattributed historical PIDs are not guessed to be TAZ workers.')
        if latency:
            st.subheader('Latency along the data path')
            st.caption('Real wall-clock milliseconds. These are overlapping intervals; do not add them together. Grafana timing ends at an instrumented query response, not browser rendering. The initial snapshot can include startup delays; samples within a run are correlated.')
            left,right=st.columns(2)
            for column,name in zip((left,right),PATHS[:2]):
                with column:
                    st.markdown('**'+metric_info(name)['label']+'**')
                    rows=[dict(Run=r['architecture'].title()+' · '+r['run'],p50_ms=r['p50'],p95_ms=r['p95'],samples=r['count']) for r in latency if r['stage']==name]
                    if rows:
                        st.bar_chart(pd.DataFrame(rows),x='Run',y=['p50_ms','p95_ms'])
                        st.caption(metric_info(name)['meaning'])
                    else: st.info('No recorded samples. Run a new instrumented simulation'+(' with agents.' if name==PATHS[1] else '.'))
            with st.expander('Latency metric guide — start, end and meaning'):
                definitions=[metric_info(name) for name in sorted({r['stage'] for r in latency}|set(PATHS))]
                for category in GROUPS[:-1]:
                    entries=[d for d in definitions if d['group']==category]
                    if entries: st.markdown('**'+category+'**\n\n'+'\n'.join(f"- **{d['label']}** (`{d['metric']}`): {d['meaning']}" for d in entries))
                st.caption('Persistence timing uses the first successful historical readback, not the exact database commit timestamp. No-agent runs have no policy-action latency. Six cycles provide few samples for tail estimates.')
            group=st.selectbox('Latency detail',GROUPS,index=0 if any(r['stage'] in PATHS for r in latency) else 1)
            details=[]
            for row in latency:
                info=metric_info(row['stage'])
                if group!='All metrics' and info['group']!=group: continue
                details.append(dict(run=row['run'],architecture=row['architecture'],measurement=info['label'],samples=row['count'],
                    median_ms=row['p50'],p95_ms=row['p95'],max_ms=row['max']))
            if details: st.dataframe(details,width='stretch',hide_index=True)
            for info in reports:
                if info.get('grafana_probe_failures'): st.warning(info['run']+': some Grafana observations were not confirmed; check the exported observer errors before comparing that path.')
        if totals:
            st.caption('Whole-operation resource accounting: sampled CPU seconds, memory peaks, disk I/O and container-interface traffic. Missing counters remain unavailable; sampling can miss short processes and peaks.')
            st.dataframe(totals,width='stretch',hide_index=True)
    st.caption('ZIP exports include CSV measurements and a latency guide, JSON configuration/summary, and standalone PNG/SVG plots, including data-path-latency. Application RSS and Docker memory remain separate; polling can miss short peaks.')


def app(mode):
    st.set_page_config(page_title=f'BoMoDT | {mode.title()}',page_icon='🚦',layout='wide')
    st.title('Bologna Mobility Digital Twin')
    st.caption(f'{mode.title()} control room · GreenWave road network')
    @st.fragment(run_every='15s')
    def services_indicator():
        state=cached_service_status(mode)
        icons={'ready':'🟢','partial':'🟡','stopped':'🔴','unknown':'⚪'}
        label={'ready':'Services running','partial':'Services partially available / starting',
               'stopped':'Services stopped','unknown':'Service status unavailable'}
        st.markdown(f"{icons[state['state']]} **{label[state['state']]}** · {state['running'] if state['running'] is not None else '?'} / {state['expected']} containers")
        st.caption('Updated every 15 seconds. Container state only; emulation also checks broker/database readiness.')
    services_indicator()
    with st.sidebar:
        st.header(mode.title()+' deployment')
        st.caption('One city-wide FIWARE stack.' if mode=='monolithic' else 'Eleven independent FIWARE stacks, one per trained TAZ.')
        st.caption('Start services before emulating sensor data. Stopping keeps the stored data.')
        if st.button('Start services',type='primary',width='stretch'):
            launch(mode,'stack',['stack','up','--mode',mode])
        if st.button('Stop services',width='stretch'):
            launch(mode,'stack',['stack','stop','--mode',mode])
        taz=st.selectbox('TAZ to view',TAZS)
        st.caption('Technical checks and detailed logs are in Diagnostics.')
    emulation_tab,monitor_tab,scenario_tab,run_tab,metrics_tab,diagnostics_tab=st.tabs([
        'City emulation','FIWARE & Grafana','SUMO scenarios','Simulation','Footprint & latency','Diagnostics'])
    with emulation_tab:
        st.subheader('Emulate traffic sensors')
        st.write('Choose any available source date and starting hour. Counts are distributed over the reporting intervals, with reproducible fluctuations and the exact hourly total.')
        source=st.selectbox('Source dataset',[str(p) for p in sorted((ROOT/'data').glob('processed_traffic_flow*.csv'))],
            format_func=lambda p:Path(p).name)
        if source:
            days=catalog(source,Path(source).stat().st_mtime_ns)
            if days:
                c1,c2,c3=st.columns(3)
                day=c1.selectbox('Start date',days)
                hour=c2.number_input('Start hour (Europe/Rome)',min_value=0,max_value=23,value=8,step=1)
                duration=c3.number_input('Duration (source hours)',min_value=1,max_value=48,value=1,step=1)
                start=f'{day}T{hour:02d}:00'
                temporal=st.toggle('Distribute readings over time',value=True)
                speed=st.number_input('Playback speed (source seconds per wall second)',min_value=1.,value=60.,step=10.,disabled=not temporal,
                    help='1 is real time; 60 replays a source hour in about one minute, plus processing time.')
                with st.expander('Transmission settings'):
                    interval=st.selectbox('Report every (source seconds)',[60,120,300,600])
                    delay=st.number_input('Additional transmission delay (ms)',min_value=0.,value=0.,step=5.)
                    seed=st.number_input('Traffic fluctuation seed',min_value=0,value=42,step=1)
                    st.caption('Sub-hour fluctuations are synthesized from hourly data. Individual real vehicle arrival times are not available.')
                valid=False
                try:
                    preview=preview_period(source,Path(source).stat().st_mtime_ns,start,duration)
                    valid=True
                    a,b=st.columns(2)
                    a.metric('Sensors',len({o['device_id'] for o in preview}))
                    b.metric('Detector passages (source counts)',int(sum(o['traffic_flow'] for o in preview)))
                    with st.expander('Source count preview'): st.dataframe(preview,width='stretch',hide_index=True)
                except Exception as error: st.error(str(error))
                if st.button('Start emulation',type='primary',disabled=not valid):
                    args=['emulate','--mode',mode,'--source',source,'--start-time',start,'--duration-hours',str(duration),
                        '--speed',str(speed),'--interval-seconds',str(interval),'--seed',str(seed),'--delay-ms',str(delay)]
                    if temporal: args.append('--temporal')
                    launch(mode,'emulate',args)
        @st.fragment(run_every='3s')
        def progress():
            latest=document_paths(ROOT/'sessions'/mode,'manifest.json')
            if latest:
                path,data=latest[0]
                state_path=path.with_name('progress.json')
                if state_path.exists():
                    state=json.loads(state_path.read_text())
                    st.progress(state['completed_buckets']/state['planned_buckets'],text='Source time: '+state['source_time']+' · '+data['status'])
        progress()
    sessions=document_paths(ROOT/'sessions'/mode,'manifest.json')
    with monitor_tab:
        st.subheader('Traffic observations in FIWARE')
        control_runs=document_paths(ROOT/'experiments','control-run.json',lambda d:d.get('mode')==mode)
        view=st.radio('FIWARE data',['Traffic emulation','Simulation control'],horizontal=True) if control_runs else 'Traffic emulation'
        if view=='Simulation control':
            index=st.selectbox('Simulation run',range(len(control_runs)),format_func=lambda i:f"{control_runs[i][0].parent.name} · {control_runs[i][1]['status']}")
            path,run=control_runs[index]
            st.caption(f"Tenant: {run['tenant']} · Control interval: {run['step_seconds']} simulated seconds. Refresh workspace to update run progress.")
            start=datetime.fromisoformat(run['source_start'].replace('Z','+00:00')).timestamp()*1000
            query=urlencode({'orgId':1,'from':int(start),'to':int(start+3600_000),
                'var-tenant':'mt'+run['tenant'],'var-taz':taz,'kiosk':''})
            url=endpoints(mode,taz)['grafana']+'/d/bologna-control/bologna-simulation-control?'+query
            st.markdown('[Open simulation Grafana]('+url+')')
            if hasattr(st,'iframe'): st.iframe(url,height=760)
            else:
                import streamlit.components.v1 as components
                components.iframe(url,height=760,scrolling=True)
        elif sessions:
            selected=st.selectbox('Replay to monitor',range(len(sessions)),format_func=lambda i:
                f"{sessions[i][1]['day']} · {sessions[i][1]['status']} · {sessions[i][1]['tenant']}")
            path,manifest=sessions[selected]
            choices=periods(manifest)
            selected_period=st.selectbox('Source period',range(len(choices)),format_func=lambda i, choices=choices:f"{choices[i]['day']} {choices[i]['hour']:02d}:00")
            period=choices[selected_period]
            if manifest.get('error'): st.error(manifest['error'])
            start,end=time_window(period['day'],period['hour'])
            millis=lambda s:int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000)
            query=urlencode({'orgId':1,'from':millis(start),'to':millis(end),
                'var-tenant':'mt'+manifest['tenant'],'var-taz':taz,'kiosk':''})
            url=endpoints(mode,taz)['grafana']+'/d/bologna-traffic/bologna-traffic-observations?'+query
            st.markdown('[Open Grafana]('+url+')')
            if hasattr(st,'iframe'): st.iframe(url,height=760)
            else:
                import streamlit.components.v1 as components
                components.iframe(url,height=760,scrolling=True)
            st.caption('The graphs use the source date and selected TAZ. Timed reports show interval crossings and cumulative hourly counts.')
        else: st.info('Start an emulation to monitor its ingested traffic data.')
    with scenario_tab:
        st.subheader('Prepare a SUMO scenario')
        with st.expander('Manage saved scenarios'):
            count=len(saved_scenarios(ROOT))
            st.caption(f'{count} saved scenarios, shared by both dashboards. Deletion keeps replay data, experiment results and trained models. Exported results remain readable, but deleted scenarios cannot be rerun.')
            confirmed=st.checkbox('I want to permanently delete all saved scenarios',key='confirm_wipe')
            if st.button('Delete all saved scenarios',disabled=not confirmed or count==0):
                try:
                    removed=wipe_scenarios(ROOT)
                    st.session_state['scenario_cleanup_notice']=f'Deleted {removed} saved scenarios.'
                    st.rerun()
                except Exception as error: st.error(str(error))
            if 'scenario_cleanup_notice' in st.session_state: st.success(st.session_state.pop('scenario_cleanup_notice'))
        verified=[s for s in sessions if s[1]['status']=='verified']
        if verified:
            choice=st.selectbox('Verified replay',range(len(verified)),format_func=lambda i:verified[i][1]['tenant'])
            manifest_path,manifest=verified[choice]
            choices=periods(manifest)
            selected_period=st.selectbox('Hour to simulate',range(len(choices)),format_func=lambda i, choices=choices:f"{choices[i]['day']} {choices[i]['hour']:02d}:00",key='scenario_period')
            period=choices[selected_period]
            profiles=json.loads((ROOT/'configs/training-profiles.json').read_text())
            profile=st.selectbox('RL profile / reference demand settings',list(profiles))
            with st.expander('Route fitting options'):
                noise=st.number_input('Demand multiplier',min_value=.01,value=1.,step=.05)
                route_seed=st.number_input('Route seed',min_value=0,value=42,step=1)
                candidates=st.text_input('Existing candidate routes (optional)',value='')
            target=training_demand(profile,period['day'],period['hour'],noise,enforce_training_hours=False)
            st.metric('Target scenario vehicles',target)
            st.caption('The emulation period is independent of training. Learned-agent deployment is available when the prepared hour matches its profile.')
            if st.button('Prepare scenario',type='primary'):
                args=['prepare','--manifest',str(manifest_path),'--date',period['day'],'--hour',str(period['hour']),
                    '--profile',profile,'--noise',str(noise),'--seed',str(route_seed),'--output',str(new_path('scenarios',mode))]
                if candidates: args+=['--candidates',candidates]
                launch(mode,'prepare',args)
        else: st.info('Complete an emulation so its full hourly counts are verified in FIWARE.')
    with run_tab:
        st.subheader('Run SUMO')
        scenarios=document_paths(ROOT/'scenarios','scenario.json',lambda d:d['status']=='ready')
        if scenarios:
            selected=st.selectbox('Prepared scenario',range(len(scenarios)),format_func=lambda i:
                f"{scenarios[i][1]['day']} {scenarios[i][1]['hour']:02d}:00 · {scenarios[i][0].parent.name}")
            scenario_path,meta=scenarios[selected]
            control=st.radio('Traffic control',['none','local','coordinated'],horizontal=True,
                format_func=lambda c:{'none':'No agents','local':'Local agents','coordinated':'Local agents + coordinator'}[c])
            gui=st.checkbox('Open SUMO GUI',value=False)
            gui_delay=st.number_input('GUI playback delay per simulation step (ms)',min_value=0.,value=20.,step=5.,disabled=not gui)
            if gui: st.caption('A desktop window opens on the machine running Streamlit. It starts automatically and closes at the end. GUI delay is included in experiment metadata.')
            with st.expander('Advanced simulation options'):
                offline=st.checkbox('Offline baseline (bypass FIWARE during simulation)',value=False)
                replicas=st.checkbox('Add an independent full-city simulation per TAZ',value=False,disabled=mode!='federated' or control=='none')
                st.caption('Replicated physics launches 12 SUMO processes; the GUI displays the authoritative city only.')
                st.json({k:meta[k] for k in ['profile','vehicle_count','actual_route_vehicle_count','network_sha256']})
            eligible=True
            if control!='none':
                try: training_demand(meta['profile'],meta['day'],meta['hour'],meta.get('demand_noise',1.))
                except ValueError as error: eligible=False;st.info(str(error)+'. Select No agents to run this source hour.')
            if st.button('Start simulation',type='primary',disabled=not eligible):
                args=['simulate','--mode',mode,'--scenario',str(scenario_path.parent),'--control',control,
                    '--output',str(new_path('experiments',mode))]
                if replicas and control!='none' and mode=='federated': args.append('--replicas')
                if gui: args+=['--gui','--gui-delay',str(gui_delay)]
                if offline: args.append('--offline')
                launch(mode,'simulate',args)
        else: st.info('Prepare a scenario from a verified replay first.')
        st.caption('Default: active FIWARE data path, six 600-second control cycles plus a terminal snapshot. Start services before running. SUMO waits for each control/history barrier. Whole-operation resources and transmission latencies are in Footprint & latency.')
    with metrics_tab: footprint_tab(mode)
    with diagnostics_tab:
        st.subheader('Diagnostics and advanced inspection')
        action=st.selectbox('Inspection',['Service health','Container status and logs','Broker entities','Container resource snapshot','Generate Compose files'])
        if st.button('Run inspection'):
            try:
                if action=='Service health': st.json(FiwareClient(mode,taz,'gw_health').health())
                elif action=='Generate Compose files': st.success(str(generate(mode)))
                elif action=='Broker entities':
                    if not sessions: st.info('No replay tenant is available yet.')
                    else: st.dataframe(FiwareClient(mode,taz,sessions[0][1]['tenant']).entities(),width='stretch')
                else:
                    for command in ([compose_command(mode,'stats')] if action=='Container resource snapshot' else
                        [compose_command(mode,'ps'),compose_command(mode,'logs')+[service_key(mode,taz,s) for s in ['orion','iot','quantumleap']]]):
                        result=subprocess.run(command,capture_output=True,text=True,timeout=20)
                        st.code(result.stdout+result.stderr,language='text')
            except Exception as error: st.error(str(error))
        @st.fragment(run_every='3s')
        def job_details():
            for job in list_jobs(mode)[:5]:
                with st.expander(f"{job['kind'].title()} · {job['status']} · {job['id']}",expanded=job['status']=='failed'):
                    st.code(tail_log(job) or 'Starting…',language='text')
        job_details()
    st.divider()
    @st.fragment(run_every='3s')
    def operation_status():
        jobs=list_jobs(mode)
        active=next((j for j in jobs if j['status'] in ('starting','running')),None)
        if active:
            st.info(active['kind'].title()+' is running. Detailed logs are in Diagnostics.')
            if active['status']=='running' and st.button('Cancel current operation'):
                try: stop_job(active)
                except Exception as error: st.error(str(error))
        elif jobs: st.caption('Last operation: '+jobs[0]['kind']+' · '+jobs[0]['status'])
    operation_status()
    st.button('Refresh workspace',help='Reload completed replay, scenario and report lists.')
