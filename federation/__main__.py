"""Run identical control barriers in one process or across independent TAZ services."""
import argparse
import hashlib
import importlib.metadata
import platform
from datetime import datetime
import json
from pathlib import Path
import time
from .models import ROOT
from .node import Fleet
from .metrics import ResourceMonitor, distribution


def run(args):
    output = Path(args.output).resolve()
    # Prevent accidentally mixing histories and resource traces from different runs.
    output.mkdir(parents=True, exist_ok=False)
    spec = vars(args).copy(); spec['output'] = str(output)
    spec['role'] = 'local_controller' if args.control != 'coordinated' else 'coordinated_controller'
    city, fleet, bus = None, None, None
    environment = {'python':platform.python_version(), 'platform':platform.platform(), 'packages':{}}
    for package in ['torch','torchrl','tensordict','libtraci','psutil']:
        try:
            environment['packages'][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            environment['packages'][package] = None
    (output/'environment.json').write_text(json.dumps(environment,indent=2))
    route_hash = hashlib.sha256(Path(args.routes).read_bytes()).hexdigest() if args.routes else None
    resources = ResourceMonitor(output/'resources.json')
    records = []
    start = time.perf_counter_ns()
    try:
        if args.demo:
            ids = ['F','B','H']
            state = {'obs':[[0.7],[0.3],[0.9]], 'mask':[[True,False]]*3}
            c = None
        else:
            import torch
            from .models import load_checkpoint, coordinator
            from .simulation import City
            from rl_module.coordination import CoordinationObservationConfig, build_coordination_observation
            torch.set_num_threads(1)
            c = load_checkpoint(args.profile, spec['role'])
            city = City(spec, output/'world')
            ids = city.ids
            state = city.reset()
            if args.control == 'coordinated':
                global_policy = coordinator(c)
                # Reuse the actual graph saved with the training, avoiding geometry approximation drift.
                neighbors = c['adjacency_metadata']['neighbors_by_taz']
                adjacency = torch.tensor([[float(b in neighbors[a]) for b in ids] for a in ids])
                obs_config = CoordinationObservationConfig(**c['coordination_observation_config'])
                previous_prices = torch.zeros(len(ids))
                previous_flow = torch.zeros((len(ids),len(ids)))
                previous_summary = None
        specs = [dict(spec,taz=taz,port=args.dashboard_port+i if args.dashboard_port else 0) for i,taz in enumerate(ids)]
        fleet = Fleet(specs, args.mode, args.timeout)
        import os
        try:
            import psutil
            components=[dict(pid=os.getpid(),create_time=psutil.Process().create_time(),taz=None,
                component='Central runtime: local policies + coordinator + world' if args.mode=='monolithic' else 'Global runtime: coordinator + world')]
            components.extend(dict(pid=p.pid,create_time=psutil.Process(p.pid).create_time(),taz=taz,component='Local worker / '+taz)
                for p,taz in zip(fleet.processes,ids))
        except ImportError: components=[]
        (output/'process-components.json').write_text(json.dumps(components,indent=2))
        if args.fiware_tenant:
            from .fiware.control import ControlBus
            bus=ControlBus(spec,ids)
        startup_ms = (time.perf_counter_ns()-start)/1e6
        print('TAZ services:', ', '.join(f'{t}:http://127.0.0.1:{s["port"]}' if s['port'] else t for t,s in zip(ids,specs)), flush=True)
        for seq in range(args.steps):
            cycle = time.perf_counter_ns()
            if bus:
                received=bus.exchange('observation',seq,[dict(observation=state['obs'][i],mask=state['mask'][i],flow_row=state['flow'][i],generated_ns=state['generated_ns']) for i in range(len(ids))])
                # Establish a measured durable-observation boundary before any
                # policy evaluation. A broker ACK alone is not a DB commit.
                bus.verify_history(seq,stage='observation')
                # Coordinator input must come from the broker, not the original
                # in-memory state used by the simulated sensor publisher.
                state={'obs':[r['observation'] for r in received],'mask':[r['mask'] for r in received],
                       'flow':[r['flow_row'] for r in received]}
                if args.control=='coordinated': previous_flow=torch.tensor(state['flow'],dtype=torch.float32)
            prices, contexts = [0.]*len(ids), [[] for _ in ids]
            coordinator_start = time.perf_counter_ns()
            if not args.demo and args.control == 'coordinated':
                with torch.inference_mode():
                    obs = torch.tensor(state['obs'])
                    offset = city.env.max_tls_per_taz * city.env.per_tls_feature_dim
                    features = obs[:,offset:offset+city.env.per_taz_feature_dim]
                    coord_obs = build_coordination_observation(features, adjacency,
                        previous_flow_matrix=previous_flow, previous_prices=previous_prices,
                        previous_action_summary=previous_summary, taz_ids=ids,
                        extra_context=torch.tensor([args.hour/23., args.vehicle_count/c['demand_settings']['base_demand'], args.demand_noise, 0.]),
                        config=obs_config, price_max=max(c['coordinator_settings']['price_bins']))
                    index,_,_ = global_policy.act(coord_obs, torch.ones((1,len(ids)),dtype=torch.bool), deterministic=True)
                    price_tensor = global_policy.action_values(index).squeeze(0)
                    norm = price_tensor/max(c['coordinator_settings']['price_bins'])
                    neighbor = adjacency.matmul(norm)/adjacency.sum(-1).clamp_min(1.)
                    contexts = torch.stack([norm,neighbor,norm-neighbor],-1).tolist()
                    prices = price_tensor.tolist()
            coordinator_ms = (time.perf_counter_ns()-coordinator_start)/1e6
            if bus:
                bus.exchange('coordination',seq,[dict(price=prices[i],price_context=contexts[i]) for i in range(len(ids))])
            requests = [dict(op='observe',event=dict(taz=taz,seq=seq,simulation_seconds=seq*args.step_seconds,
                        created_ns=cycle,observation=state['obs'][i],mask=state['mask'][i],price=prices[i],price_context=contexts[i])) for i,taz in enumerate(ids)]
            if bus:
                for req in requests:
                    for key in ('observation','mask','price','price_context'): req['event'].pop(key)
            events = fleet.batch(requests)
            decision_ns = time.perf_counter_ns()
            actions = [e['action'] for e in events]
            commands=bus.read_commands(seq,events) if bus else None
            if bus: actions=[c['action'] for c in commands]
            if args.control == 'fixed':
                zero = c['local_controller_settings']['action_bins'].index(0.) if c else 1
                actions = [[zero]*len(a) for a in actions]
            if args.demo:
                advance = {'wall_ms':0.,'done':False}
            else:
                if bus:
                    def before_advance():
                        feedback=city.feedback(actions)
                        now=time.perf_counter_ns()
                        for node,command in zip(bus.nodes,commands):
                            node.log(metric='command_to_readback_ms',value_ms=(now-command['created_ns'])/1e6,seq=seq)
                            confirmed=bus.persisted_confirmed_ns[node.event_id('observation',seq)]
                            node.log(metric='persisted_observation_to_actuation_ms',value_ms=(now-confirmed)/1e6,
                                seq=seq,persistence_confirmed_ns=confirmed,actuation_readback_ns=now)
                            generated=bus.observation_sources[node.taz]
                            node.log(metric='observation_to_actuation_ms',value_ms=(now-generated)/1e6,
                                seq=seq,generated_ns=generated,actuation_readback_ns=now)
                        feedback[:]=bus.exchange('feedback',seq,feedback)
                        # Keep applied-action context from the broker-confirmed
                        # feedback, preserving the training's padded group order.
                        city.fiware_feedback=feedback
                        bus.verify_history(seq)
                        bus.nodes[0].log(metric='control_closed_loop_ms',value_ms=(time.perf_counter_ns()-cycle)/1e6,seq=seq)
                    city.before_advance=before_advance
                advance = city.advance(actions)
                city.before_advance=None
                if bus:
                    advance['wall_ms']=city.step_events[-1]
                    bus.nodes[0].log(metric='physics_advance_ms',value_ms=advance['wall_ms'],seq=seq)
            fleet.batch([dict(op='feedback',seq=seq,feedback=dict(
                status='synthetic' if args.demo else 'sumo-readback',
                groups=city.env.last_group_action_details.get(taz,[]) if city else [],
                requested_action_indices=actions[i])) for i,taz in enumerate(ids)])
            feedback_ns = time.perf_counter_ns()
            if args.replica:
                fleet.batch([dict(op='advance',actions=actions) for _ in ids])
            record = dict(seq=seq,coordinator_ms=coordinator_ms,decision_barrier_ms=(decision_ns-cycle)/1e6,
                          control_to_next_observation_ms=(feedback_ns-cycle)/1e6,
                          simulation_ms=advance['wall_ms'],cycle_ms=(time.perf_counter_ns()-cycle)/1e6,
                          actions=actions,prices=prices,
                          local_inference_ms=[e['inference_ms'] for e in events],
                          local_inference_cpu_ms=[e['inference_cpu_ms'] for e in events])
            records.append(record)
            with (output/'cycles.jsonl').open('a') as handle:
                handle.write(json.dumps(record)+'\n')
            if not args.demo:
                if args.control == 'coordinated':
                    from rl_module.coordination import summarize_step_actions
                    previous_prices = price_tensor
                    previous_flow = torch.tensor(city.env.get_last_step_flow_matrix(),dtype=torch.float32)
                    if bus:
                        width=len(state['mask'][0])
                        applied=torch.zeros((len(ids),width));deltas=torch.zeros_like(applied)
                        for i,feedback in enumerate(city.fiware_feedback):
                            for group in feedback['groups']:
                                g=group['group_index'];applied[i,g]=group['requested_delta'];deltas[i,g]=group['applied_delta_mean']
                        previous_summary=summarize_step_actions(ids,applied,deltas,torch.tensor(state['mask']))
                    else: previous_summary = summarize_step_actions(ids,city.td['applied_action'],city.td['applied_duration_delta'],torch.tensor(state['mask']))
                state = city.snapshot()
                if advance['done'] and not bus:
                    break
        if bus:
            terminal_seq=len(records)
            bus.exchange('observation',terminal_seq,[dict(observation=state['obs'][i],mask=state['mask'][i],flow_row=state['flow'][i],terminal=True,generated_ns=state['generated_ns']) for i in range(len(ids))])
            bus.verify_history(terminal_seq)
        # Terminal environment.step() already closed TraCI to flush SUMO outputs.
        # Use the time measured immediately after the last physics advance.
        simulated_end_seconds=None if args.demo else city.simulated_seconds
        if bus and abs(simulated_end_seconds-args.end)>.001:
            raise RuntimeError('Authoritative SUMO time differs from the configured control horizon')
        if args.hold_seconds:
            print(f'Dashboards remain available for {args.hold_seconds}s.', flush=True)
            time.sleep(args.hold_seconds)
    except BaseException as error:
        (output/'failure.json').write_text(json.dumps({'status':'failed','error':str(error),'config':vars(args)}))
        raise
    finally:
        if bus: bus.close()
        if fleet:
            fleet.close()
        if city:
            city.close()
        resources.close()
    summary = dict(config=vars(args),routes_sha256=route_hash,taz_ids=ids,startup_ms=startup_ms,
                   completed_cycles=len(records),status='completed',simulated_end_seconds=simulated_end_seconds,
                   latency_ms={key:distribution([r[key] for r in records]) for key in
                       ['coordinator_ms','decision_barrier_ms','control_to_next_observation_ms','simulation_ms','cycle_ms']})
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary['latency_ms'], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gui',action='store_true',help='Display the authoritative world in SUMO GUI')
    parser.add_argument('--fiware-tenant',help='Run-scoped tenant for the synchronous FIWARE control loop')
    parser.add_argument('--fiware-start',default='2025-01-01T00:00:00Z',help='UTC source timestamp at simulated t=0')
    parser.add_argument('--gui-delay',type=float,default=20,help='GUI playback delay per simulation step in milliseconds')
    parser.add_argument('--demo',action='store_true',help='Synthetic infrastructure smoke test; no SUMO or learned policies')
    parser.add_argument('--mode',choices=['monolithic','federated'],default='federated')
    parser.add_argument('--profile',choices=list(json.loads((ROOT/'configs/models.json').read_text())),default='200_weekdays_8_17')
    parser.add_argument('--control',choices=['coordinated','local','fixed'],default='coordinated')
    parser.add_argument('--replica',action='store_true',help='One independent full-city SUMO replay per TAZ in addition to the world')
    parser.add_argument('--routes',help='Existing SUMO .rou.xml; use the same file for every comparison')
    parser.add_argument('--output',default='runs/'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    parser.add_argument('--steps',type=int,default=6)
    parser.add_argument('--step-seconds',type=int,default=600)
    parser.add_argument('--end',type=int,default=3600)
    parser.add_argument('--hour',type=int,default=8)
    parser.add_argument('--vehicle-count',type=int,default=1000,help='Actual demand count, used in the pretrained coordinator context')
    parser.add_argument('--demand-noise',type=float,default=1.)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--delay-ms',type=float,default=0.)
    parser.add_argument('--timeout',type=float,default=120.)
    parser.add_argument('--dashboard-port',type=int,default=0,help='First localhost port; 0 disables dashboards')
    parser.add_argument('--hold-seconds',type=float,default=0.)
    args = parser.parse_args()
    if args.fiware_tenant and (args.demo or args.control=='fixed'):
        parser.error('FIWARE control requires a real local/coordinated policy run')
    if args.fiware_tenant and args.steps*args.step_seconds!=args.end:
        parser.error('FIWARE control steps must cover the configured simulation horizon exactly')
    if args.gui_delay<0: parser.error('GUI delay must be nonnegative')
    if args.steps <= 0 or args.step_seconds <= 0 or args.end <= 0 or args.delay_ms < 0 or args.hold_seconds < 0 or args.timeout <= 0:
        parser.error('Durations and counts must be positive (delay/hold may be zero)')
    if not 0 <= args.hour <= 23:
        parser.error('hour must be between 0 and 23')
    if args.replica and (args.demo or args.mode != 'federated'):
        parser.error('--replica requires a non-demo federated run')
    if not args.demo and (not args.routes or not Path(args.routes).is_file()):
        parser.error('A real run requires an existing --routes file')
    run(args)


if __name__ == '__main__':
    main()
