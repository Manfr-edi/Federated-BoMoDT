"""Replay real detectors through IoT Agent, with verified source-hour barriers."""
from collections import defaultdict
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import uuid
from ..models import ROOT, VENDOR
from .client import FiwareClient
from .history import read_session, IncompleteHistoryError
from .observations import read_observations, digest, slot
from .timing import read_period, timed_events
from ..metrics import ResourceMonitor
from .container_metrics import monitor_containers
from .deployment import validate_mode


def save(path, payload):
    temporary=Path(str(path)+'.tmp')
    temporary.write_text(json.dumps(payload,indent=2)+'\n')
    temporary.replace(path)


def replay(mode, source, day, hours, delay_ms=0, verify_timeout=120, session_dir=None,
           client_factory=FiwareClient, history_reader=read_session, *,
           start_time=None, duration_hours=1, temporal=False, speed=60., interval_seconds=60, seed=42):
    validate_mode(mode)
    if client_factory is FiwareClient and history_reader is read_session:
        import psycopg  # Fail before sending data if the history driver is missing.
    if delay_ms<0 or verify_timeout<0: raise ValueError('Delay/timeout must be nonnegative')
    if speed<=0 or not math.isfinite(speed): raise ValueError('Playback speed must be finite and positive')
    observations=read_period(source,start_time,duration_hours) if start_time else read_observations(source,day,hours)
    from zoneinfo import ZoneInfo
    local_periods=[datetime.fromisoformat(o.observed_at.replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Rome')) for o in observations]
    periods=list(dict.fromkeys((d.date().isoformat(),d.hour) for d in local_periods))
    day=periods[0][0]
    hours=sorted({h for _,h in periods})
    if temporal and interval_seconds not in (60,120,300,600): raise ValueError('Invalid reporting interval')
    tenant='gw_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:8]
    output=Path(session_dir or ROOT/'sessions'/mode/tenant).resolve()
    output.mkdir(parents=True,exist_ok=False)
    manifest=dict(version=1,transport_schema_version=2,tenant=tenant,mode=mode,status='provisioning',
        day=day,hours=hours,periods=[dict(day=d,hour=h) for d,h in periods],
        completed_hours=[],hourly_verification=[],pacing=dict(temporal=temporal,speed=speed if temporal else None,
            interval_seconds=interval_seconds if temporal else None,seed=seed),
        source_csv=str(Path(source).resolve()),source_sha256=digest(source),
        network_sha256=digest(VENDOR/'sumoenv/static/full.net.xml'),
        observations=[o.to_dict() for o in observations],created_at=datetime.now(timezone.utc).isoformat())
    path=output/'manifest.json'
    save(path,manifest)
    groups=defaultdict(list)
    owner=lambda o:'city' if mode=='monolithic' else o.taz
    for o in observations: groups[owner(o)].append(o)
    transmission_seconds=0.
    monitors=ExitStack()
    if client_factory is FiwareClient:
        monitors.enter_context(monitor_containers(mode,output))
        monitor=ResourceMonitor(output/'resources.json');monitors.callback(monitor.close)
    print(f'Emulation session: {path}',flush=True)

    def provision_group(pair):
        key,items=pair
        client=client_factory(mode,items[0].taz,tenant)
        try:
            client.wait_ready()
            client.provision(list({o.device_id:o for o in items}.values()))
        except Exception as error:
            raise RuntimeError(f'FIWARE provisioning failed: mode={mode}, owner={key}, '
                               f'tenant={tenant}: {error}') from error
        return key,client

    try:
        started=time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(groups)) as pool:
            clients=dict(pool.map(provision_group,groups.items()))

            pacing_origin=time.perf_counter_ns()

            def transmit(items, attempt, source_offset=None):
                nonlocal transmission_seconds
                batches=defaultdict(list)
                for o in items: batches[owner(o)].append(o)
                def send_batch(pair):
                    key,batch=pair
                    trace=[]
                    for o in batch:
                        scheduled_ns=pacing_origin+int(source_offset/speed*1e9) if source_offset is not None else None
                        if delay_ms: time.sleep(delay_ms/1000)
                        sent_ns=time.perf_counter_ns()
                        accepted_ms=clients[key].send(o)
                        ack_ns=time.perf_counter_ns()
                        trace.append(dict(sample_id=o.sample_id,taz=o.taz,observed_at=o.observed_at,
                            attempt=attempt,iot_accept_ms=accepted_ms,flow_increment=o.flow_increment,
                            hour_complete=o.hour_complete,scheduled_wall_ns=scheduled_ns,
                            send_started_ns=sent_ns,iot_ack_ns=ack_ns,
                            schedule_lateness_ms=max(0,(sent_ns-scheduled_ns)/1e6) if scheduled_ns is not None else None))
                    return trace
                before=time.perf_counter()
                # Append once; rewriting the entire trace on every minute would
                # make measurement overhead dominate long emulations.
                with (output/'ingestion.jsonl').open('a') as handle:
                    for trace in pool.map(send_batch,batches.items()):
                        for event in trace: handle.write(json.dumps(event)+'\n')
                transmission_seconds+=time.perf_counter()-before

            # Verify one source hour before replacing broker entities with the
            # next hour. HTTP acceptance alone is not historical persistence.
            for period_index,(period_day,hour) in enumerate(periods):
                from .observations import time_window
                start_stamp,_=time_window(period_day,hour)
                batch=[o for o in observations if o.observed_at==start_stamp]
                hourly_started=time.perf_counter()
                if temporal:
                    schedules=[list(timed_events(o,interval_seconds,seed)) for o in batch]
                    for bucket in range(3600//interval_seconds):
                        source_offset=period_index*3600+schedules[0][bucket][0]
                        target=pacing_origin+int(source_offset/speed*1e9)
                        remaining=(target-time.perf_counter_ns())/1e9
                        if remaining>0: time.sleep(remaining)
                        transmit([events[bucket][1] for events in schedules],1,source_offset)
                        save(output/'progress.json',dict(mode=mode,source_time=schedules[0][bucket][1].observed_at,
                            completed_buckets=period_index*(3600//interval_seconds)+bucket+1,
                            planned_buckets=len(periods)*(3600//interval_seconds),speed=speed))
                else: transmit(batch,1)
                verify_started=time.perf_counter()
                manifest.update(status='sent',iot_accept_seconds=transmission_seconds)
                save(path,manifest)
                deadline=time.monotonic()+verify_timeout
                repairs=0
                next_repair=time.monotonic()+.2
                while True:
                    try:
                        history_reader(manifest,period_day,hour)
                        break
                    except Exception as error:
                        if time.monotonic()>=deadline:
                            raise RuntimeError('Measurements accepted, but FIWARE persistence was not verified: '+str(error)) from error
                        if isinstance(error,IncompleteHistoryError) and time.monotonic()>=next_repair and repairs<3:
                            # Retry only missing samples through the same IoT path.
                            # deliveryId changes per attempt; sampleId/source time
                            # stay stable and the reader deduplicates identical data.
                            missing=([events[-1][1] for events in schedules if events[-1][1].hour_sample_id in error.missing_ids]
                                if temporal else [o for o in batch if o.sample_id in error.missing_ids])
                            repairs+=1
                            transmit(missing,repairs+1)
                            next_repair=time.monotonic()+.2
                        time.sleep(min(.05,max(0,deadline-time.monotonic())))
                manifest['completed_hours'].append(hour)
                manifest['hourly_verification'].append(dict(day=period_day,hour=hour,repair_batches=repairs,
                    persistence_verify_ms=(time.perf_counter()-verify_started)*1000,
                    wall_seconds=time.perf_counter()-hourly_started))
                save(path,manifest)
        manifest.update(status='verified',iot_accept_seconds=transmission_seconds,
            persistence_barrier_seconds=time.perf_counter()-started)
        save(path,manifest)
        print(f'Verified {len(observations)} persisted measurements: {path}',flush=True)
        return path
    except BaseException as error:
        manifest.update(status='failed',error=str(error));save(path,manifest)
        raise
    finally:
        monitors.close()
        if client_factory is FiwareClient:
            from .reports import export_report
            try: export_report(output,mode,'emulation')
            except Exception as error:
                save(output/'metrics-error.json',dict(error=str(error)))
                print('Metrics export failed: '+str(error),flush=True)

