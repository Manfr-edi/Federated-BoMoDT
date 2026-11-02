"""CPU interval correctness and readable, matched comparison artifacts."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from federation.fiware.reports import resource_series,export_report
from federation.fiware.comparison import compatibility,export_comparison
from federation.fiware.breakdown import role_costs


class PresentationTests(unittest.TestCase):
    def capture(self,root):
        process=dict(pid=1,create_time=1,name='python',rss=100*1024**2,uss=80*1024**2,cpu_seconds=0)
        (root/'operation-resources.json').write_text(json.dumps(dict(samples=[
            dict(wall_ns=0,processes=[process]),dict(wall_ns=1_000_000_000,processes=[]),
            dict(wall_ns=2_000_000_000,processes=[dict(process,cpu_seconds=2)])])))

    def test_missing_process_sample_uses_its_own_cpu_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.capture(root)
            row=resource_series(root)[-1]
            self.assertEqual(row['cpu_cores'],1)
            self.assertEqual(row['cpu_percent'],100)
            self.assertEqual(row['cpu_seconds_delta'],2)
            capture=json.loads((root/'operation-resources.json').read_text())
            capture['samples']=capture['samples'][::2]
            capture['samples'][0]['processes'][0]['sampled_ns']=1_000_000_000
            capture['samples'][1]['processes'][0].update(sampled_ns=2_500_000_000,cpu_seconds=1.5)
            (root/'operation-resources.json').write_text(json.dumps(capture))
            self.assertEqual(resource_series(root)[-1]['cpu_cores'],1)

    def test_role_memory_peak_is_simultaneous_not_sum_of_pid_peaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def p(pid,rss):return dict(pid=pid,create_time=1,name='python',rss=rss*1024**2,cpu_seconds=0)
            (root/'resources.json').write_text(json.dumps(dict(samples=[dict(wall_ns=0,processes=[p(1,100),p(2,10)]),dict(wall_ns=1_000_000_000,processes=[p(1,10),p(2,100)])])))
            row=role_costs(root)[0]
            self.assertEqual(row['peak_memory_mib'],110)
            self.assertNotIn('PID',row['component'])

    def test_matched_export_omits_unmeasured_charts_and_rejects_policy_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folders=[]
            for mode in ['monolithic','federated']:
                p=root/mode;p.mkdir();self.capture(p)
                (p/'summary.json').write_text(json.dumps(dict(status='completed',config=dict(mode=mode,control='local',profile='same'),
                    routes_sha256='same-route',network_sha256='same-map',taz_ids=['F'])))
                (p/'scenario-run.json').write_text(json.dumps(dict(mode=mode,control='local',integrated=False,operation_wall_seconds=2)))
                (p/'cycles.jsonl').write_text(json.dumps(dict(seq=0,local_inference_ms=[1],cycle_ms=5))+'\n')
                out=export_report(p)
                self.assertFalse((out/'data-path-latency.png').exists())
                self.assertFalse((out/'iot-agent-footprint.png').exists())
                self.assertNotIn('Unavailable',(out/'policy-footprint.svg').read_text())
                self.assertNotIn('PID ',(out/'process-footprint.svg').read_text())
                folders.append(p)
            out=export_comparison(folders,root/'comparison')
            self.assertTrue((out/'comparison.pdf').read_bytes().startswith(b'%PDF'))
            with zipfile.ZipFile(out/'comparison.zip') as archive:
                self.assertIn('coverage.csv',archive.namelist())
                self.assertNotIn('comparison-policy-work.png',archive.namelist())
            reports=[json.loads((p/'metrics/summary.json').read_text()) for p in folders]
            reports[1]['control']='none'
            self.assertIn('Different control',compatibility(reports))
