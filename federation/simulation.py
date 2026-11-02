"""Authoritative city physics and optional independent full-network twin replicas.

Replicas replay the city action barrier. They preserve cross-boundary traffic;
they are NOT partitioned-network co-simulation and do not reduce SUMO workload.
"""
import json
import time
from pathlib import Path
from xml.sax.saxutils import escape
from .models import VENDOR, load_checkpoint


class City:
    def __init__(self, spec, output):
        import torch
        import libtraci
        from libraries.classes.SumoSimulator import Simulator
        from rl_module.environment import CoordinatedTazTrafficEnv
        self.torch, self.traci = torch, libtraci
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.checkpoint = c = load_checkpoint(spec['profile'], spec['role'])
        self.ids = c['taz_ids']
        self.tls_events, self.step_events = [], []
        self.before_advance = None
        # The upstream environment closes TraCI inside its terminal step.
        # Retain the last *measured* time while the connection is still open;
        # never infer it from the requested horizon or number of decisions.
        self.simulated_seconds = None
        # Isolate detector output paths too: upstream additional files write relative paths.
        import shutil
        static = self.output / 'static'
        shutil.copytree(VENDOR / 'sumoenv/static', static, dirs_exist_ok=True)
        (self.output / 'output').mkdir(exist_ok=True)
        detector = static / 'e2Detector.xml'
        config = self.output / 'run.sumocfg'
        config.write_text(f'''<configuration><input>
<net-file value="{escape(str(static / 'full.net.xml'))}"/>
<route-files value="{escape(str(Path(spec['routes']).resolve()))}"/>
<additional-files value="{escape(str(detector))}"/>
</input><time><end value="{spec['end']}"/></time><output>
<tripinfo-output value="{self.output / 'output/tripinfos.xml'}"/>
<emission-output value="{self.output / 'output/emission-output.xml'}"/>
</output><processing><time-to-teleport value="-1"/><ignore-junction-blocker value="1"/>
<waiting-time-memory value="{c['environment_settings']['waiting_time_memory']}"/>
</processing></configuration>''')
        owner = self

        class IsolatedSimulator(Simulator):
            def start(self, **kwargs):
                if self.isLoaded():
                    self.end()
                libtraci.start([('sumo-gui' if spec.get('gui') else 'sumo'),
                               *(['--start','--quit-on-end','--delay',str(spec.get('gui_delay',20))] if spec.get('gui') else []), '-c', str(config), '--seed', str(spec['seed']),
                               '--no-step-log', 'true', '--duration-log.disable', 'true',
                               '--log', str(owner.output / 'sumo.log')])
                owner.simulated_seconds=float(libtraci.simulation.getTime())

            def step(self, quantity=1):
                # The upstream environment applies TLS changes before calling
                # this method. Confirm and persist them before physics advances.
                if owner.before_advance is not None: owner.before_advance()
                start = time.perf_counter_ns()
                super().step(quantity)
                owner.simulated_seconds=float(libtraci.simulation.getTime())
                owner.step_events.append((time.perf_counter_ns()-start)/1e6)

        class FeedbackEnvironment(CoordinatedTazTrafficEnv):
            def _apply_action_to_tls(self, tls_id, action_value):
                # The inherited actuator enforces green bounds and yellow/all-red safety.
                start = time.perf_counter_ns()
                before, _ = self._get_active_program_logic(tls_id)
                before_durations = [p.duration for p in before.phases] if before else []
                delta = super()._apply_action_to_tls(tls_id, action_value)
                logic, _ = self._get_active_program_logic(tls_id)
                durations = [p.duration for p in logic.phases] if logic else []
                owner.tls_events.append(dict(tls=tls_id, requested=action_value, applied=delta,
                    before=before_durations, readback=durations,
                    changed=before_durations != durations,
                    actuator_readback_ms=(time.perf_counter_ns()-start)/1e6))
                return delta

        self.sumo = IsolatedSimulator(str(self.output), str(self.output/'traci.log'),
                                      str(VENDOR/'data/realworlddata/taz_to_tls.json'))
        self.sumo.changeTypePath(str(self.output))
        FeedbackEnvironment.ACTION_BIN_VALUES = tuple(c['local_controller_settings']['action_bins'])
        FeedbackEnvironment.ZERO_ACTION_INDEX = FeedbackEnvironment.ACTION_BIN_VALUES.index(0.)
        kwargs = dict(c['environment_settings'])
        kwargs.update(sumo_seed=spec['seed'], cooldownSteps=spec['steps'])
        self.env = FeedbackEnvironment(self.sumo, stepSize=spec['step_seconds'],
                    selected_taz_ids=self.ids, tls_add_path=str(static/'optimized_tls.add.xml'), **kwargs)
        # Fail on topology/order drift; matching tensor width alone is insufficient.
        if self.env.get_taz_ids() != self.ids or self.env.get_tls_ids() != c['tls_ids']:
            raise ValueError('Checkpoint TAZ/TLS order differs from runtime topology')
        if self.env.get_control_groups_by_taz() != c['control_groups_by_taz']:
            raise ValueError('Checkpoint control groups differ from runtime topology')
        expected = c.get('base_obs_dim', c.get('obs_dim'))
        if self.env.agent_obs_dim != expected:
            raise ValueError('Observation width mismatch')
        self.env.set_episode_context(spec['hour'])
        self.td = None

    def reset(self):
        self.td = self.env.reset()
        return self.snapshot()

    def snapshot(self):
        return {'obs': self.td['observation'].tolist(), 'mask': self.td['action_mask'].tolist(),
                'flow':self.env.get_last_step_flow_matrix().tolist(),'generated_ns':time.perf_counter_ns()}

    def advance(self, actions):
        from tensordict import TensorDict
        start = time.perf_counter_ns()
        result = self.env.step(TensorDict({'action': self.torch.tensor(actions)}, batch_size=[]))
        self.td = result['next']
        return {'wall_ms': (time.perf_counter_ns()-start)/1e6,
                'simulation_seconds':self.simulated_seconds,
                'done': bool(self.td['terminated'].item() or self.td['truncated'].item())}

    def feedback(self,actions):
        result=[]
        for i,taz in enumerate(self.ids):
            tls_ids={tls for group in self.env.control_groups_by_taz[taz] for tls in group}
            readback=[]
            for tls in sorted(tls_ids):
                logic,_=self.env._get_active_program_logic(tls)
                readback.append(dict(tls=tls,phase=self.traci.trafficlight.getPhase(tls),
                    state=self.traci.trafficlight.getRedYellowGreenState(tls),
                    durations=[p.duration for p in logic.phases] if logic else []))
            result.append(dict(requested_action_indices=actions[i],status='sumo-readback',
                groups=self.env.last_group_action_details.get(taz,[]),tls_readback=readback))
        return result

    def close(self):
        if self.sumo.isLoaded():
            self.sumo.end()
        (self.output/'tls-feedback.json').write_text(json.dumps(self.tls_events, indent=2))
        (self.output/'simulation-step-ms.json').write_text(json.dumps(self.step_events))
