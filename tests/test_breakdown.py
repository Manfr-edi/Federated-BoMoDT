"""Distinguish physical resource ownership from logical per-TAZ inference."""
import json
from pathlib import Path
import tempfile
import unittest
from federation.fiware.breakdown import component_costs,policy_work


class BreakdownTests(unittest.TestCase):
    def test_individual_iot_agent_container_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);samples=[]
            for wall,memory in [(1_000_000_000,'20MiB / 1GiB'),(3_000_000_000,'25MiB / 1GiB')]:
                samples.append(dict(wall_ns=wall,returncode=0,docker_stats=json.dumps([
                    dict(Name='bomodt-monolithic-city-iot-1',CPUPerc='50%',MemUsage=memory)])))
            (root/'fiware-resources.json').write_text(json.dumps(dict(samples=samples)))
            row=component_costs(root)[0]
            self.assertEqual(row['service'],'iot')
            self.assertEqual(row['sampled_cpu_seconds'],1)
            self.assertEqual(row['peak_memory_mib'],25)

    def test_process_identity_cpu_deltas_and_shared_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=dict(pid=1,create_time=1,rss=100*1024**2,uss=80*1024**2,cpu_seconds=1)
            (root/'operation-resources.json').write_text(json.dumps(dict(samples=[
                dict(wall_ns=1,processes=[p]),dict(wall_ns=2,processes=[dict(p,cpu_seconds=4)])])))
            (root/'process-components.json').write_text(json.dumps([dict(pid=1,create_time=1,taz=None,component='Central runtime')]))
            rows=component_costs(root)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['sampled_cpu_seconds'],3)
            self.assertEqual(rows[0]['peak_memory_mib'],100)
            self.assertIsNone(rows[0]['taz'])  # No invented per-TAZ memory allocation.
            (root/'process-components.json').unlink()
            self.assertEqual(component_costs(root)[0]['component'],'Unattributed runtime')

    def test_policy_work_uses_measured_cpu_and_keeps_legacy_cpu_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'summary.json').write_text(json.dumps(dict(taz_ids=['F','B'])))
            cycles=[dict(local_inference_ms=[5,2],local_inference_cpu_ms=[1,.5]),
                    dict(local_inference_ms=[7,3],local_inference_cpu_ms=[2,.7])]
            path=root/'cycles.jsonl';path.write_text('\n'.join(map(json.dumps,cycles)))
            work=policy_work(root)
            self.assertEqual(work[0]['total_inference_cpu_ms'],3)
            self.assertEqual(work[0]['total_inference_wall_ms'],12)
            self.assertEqual(work[0]['mean_inference_cpu_ms'],1.5)
            for cycle in cycles:cycle.pop('local_inference_cpu_ms')
            path.write_text('\n'.join(map(json.dumps,cycles)))
            self.assertIsNone(policy_work(root)[0]['total_inference_cpu_ms'])
