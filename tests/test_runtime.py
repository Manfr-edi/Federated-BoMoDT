"""Isolation, ordering, RPC failures and checkpoint compatibility."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from federation.models import ROOT, checkpoint_path
from federation.storage import latest
from federation.node import Twin, Fleet

class InfrastructureTests(unittest.TestCase):
    def test_terminal_step_retains_measured_time_after_environment_closes_traci(self):
        try: import torch
        except ImportError: self.skipTest('PyTorch unavailable; terminal environment lifecycle NOT validated')
        from federation.simulation import City
        clock=SimpleNamespace(connected=False,seconds=0.)
        def get_time():
            if not clock.connected: raise RuntimeError('Not connected')
            return clock.seconds
        def start(*args,**kwargs): clock.connected=True;clock.seconds=0.
        traci=SimpleNamespace(start=start,simulation=SimpleNamespace(getTime=get_time))
        class Simulator:
            def __init__(self,*args): pass
            def changeTypePath(self,*args): pass
            def isLoaded(self): return clock.connected
            def end(self): clock.connected=False
            def step(self,quantity): clock.seconds+=quantity
        class Environment:
            agent_obs_dim=1
            def __init__(self,sumo,**kwargs): self.sumo=sumo
            def get_taz_ids(self): return ['F']
            def get_tls_ids(self): return ['tls']
            def get_control_groups_by_taz(self): return {'F':[['tls']]}
            def set_episode_context(self,*args): pass
            def reset(self):
                self.sumo.start();return self.state(False)
            def state(self,done):
                return dict(observation=torch.zeros((1,1)),action_mask=torch.ones((1,1),dtype=torch.bool),
                    terminated=torch.tensor(False),truncated=torch.tensor(done))
            def get_last_step_flow_matrix(self): return torch.zeros((1,1))
            def step(self,action):
                self.sumo.step(600)
                self.sumo.end()  # Same lifecycle as the upstream terminal step.
                return {'next':self.state(True)}
        checkpoint=dict(taz_ids=['F'],tls_ids=['tls'],control_groups_by_taz={'F':[['tls']]},
            obs_dim=1,environment_settings=dict(waiting_time_memory=3600),
            local_controller_settings=dict(action_bins=[-10.,0.,10.]))
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{
            'libtraci':traci,'libraries.classes.SumoSimulator':SimpleNamespace(Simulator=Simulator),
            'rl_module.environment':SimpleNamespace(CoordinatedTazTrafficEnv=Environment)}),\
                patch('federation.simulation.load_checkpoint',return_value=checkpoint),patch('shutil.copytree'):
            spec=dict(profile='fixture',role='local_controller',routes='fixture.rou.xml',end=600,
                seed=42,steps=1,step_seconds=600,hour=8)
            city=City(spec,Path(tmp)/'world')
            city.reset()
            result=city.advance([[1]])
            self.assertTrue(result['done'])
            self.assertFalse(clock.connected)
            self.assertEqual(result['simulation_seconds'],600.)
            self.assertEqual(city.simulated_seconds,600.)
            with self.assertRaisesRegex(RuntimeError,'Not connected'): get_time()
            # A reset must start a new measured clock, rather than reuse 600.
            city.reset();self.assertEqual(city.simulated_seconds,0.)
            city.close()

    def test_owner_and_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            twin=Twin(dict(output=temp,taz='F',port=0,demo=True,delay_ms=0))
            event=dict(taz='B',seq=0,observation=[1.],price_context=[],mask=[True])
            with self.assertRaises(ValueError): twin.handle(dict(op='observe',event=event))
            event['taz']='F'
            twin.handle(dict(op='observe',event=event))
            self.assertEqual(latest(twin.store.path)['taz'],'F')
            twin.handle(dict(op='feedback',seq=0,feedback={'applied':True}))
            self.assertTrue(latest(twin.store.path)['feedback']['applied'])
            with self.assertRaises(ValueError): twin.handle(dict(op='observe',event=event))
            twin.close()

    def test_remote_error_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            fleet=Fleet([dict(output=temp,taz='F',port=0,demo=True,delay_ms=0)],'federated',5)
            try:
                with self.assertRaises(RuntimeError): fleet.batch([dict(op='observe',event=dict(taz='B',seq=0))])
            finally: fleet.close()
            self.assertFalse(any(p.is_alive() for p in fleet.processes))

    def test_export_hashes(self):
        manifest=json.loads((ROOT/'configs/models.json').read_text())
        self.assertEqual(len(manifest),4)
        for profile in manifest:
            for role in manifest[profile]: self.assertTrue(checkpoint_path(profile,role).is_file())

    def test_deployment_action_parity(self):
        with tempfile.TemporaryDirectory() as temp:
            results=[]
            for mode in ['monolithic','federated']:
                out=Path(temp)/mode
                subprocess.run([sys.executable,'-m','federation','--demo','--mode',mode,'--steps','2','--output',str(out)],check=True,stdout=subprocess.DEVNULL)
                results.append([json.loads(line)['actions'] for line in (out/'cycles.jsonl').read_text().splitlines()])
            self.assertEqual(*results)

class CheckpointTests(unittest.TestCase):
    def test_strict_load_and_row_parity(self):
        try: import torch
        except ImportError: self.skipTest('PyTorch unavailable; real inference NOT validated')
        from federation.models import load_checkpoint, local_policy, coordinator
        torch.set_num_threads(1)
        for profile in json.loads((ROOT/'configs/models.json').read_text()):
            for role in ['local_controller','coordinated_controller']:
                c=load_checkpoint(profile,role); policy=local_policy(c)
                obs=torch.randn(len(c['taz_ids']),c.get('augmented_obs_dim',c.get('obs_dim')))
                mask=torch.ones(len(c['taz_ids']),6,dtype=torch.bool)
                with torch.inference_mode():
                    batch=policy.act(obs,mask,deterministic=True)[0]
                    rows=torch.cat([policy.act(o[None],m[None],deterministic=True)[0] for o,m in zip(obs,mask)])
                self.assertTrue(torch.equal(batch,rows))
                if role=='coordinated_controller': coordinator(c)

if __name__=='__main__': unittest.main()
