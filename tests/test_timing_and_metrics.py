"""Count conservation, canonical hourly history, GUI propagation and export units."""
import csv
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile
from federation.models import ROOT,VENDOR
from federation.fiware.observations import read_observations,digest
from federation.fiware.timing import minute_counts,timed_events,read_period,source_period
from federation.fiware.reports import resource_series,export_report,bytes_value


def observation():
    return replace(read_observations(ROOT/'data/processed_traffic_flow.csv','2025-01-01',[14])[0],traffic_flow=150.)


class TimingTests(unittest.TestCase):
    def test_exact_total_variable_rate_deterministic_events(self):
        o=observation();events=list(timed_events(o,60,42))
        self.assertEqual(sum(e.flow_increment for _,e in events),150)
        self.assertEqual(events[-1][1].traffic_flow,150)
        self.assertGreater(max(e.flow_increment for _,e in events),min(e.flow_increment for _,e in events))
        self.assertEqual(events,list(timed_events(o,60,42)))
        self.assertNotEqual(minute_counts(o,60,42),minute_counts(o,60,43))
        self.assertEqual(len({e.sample_id for _,e in events}),60)
        self.assertEqual(sum(e.hour_complete for _,e in events),1)
        self.assertTrue(all(e.hour_sample_id==o.sample_id for _,e in events))
        self.assertLess(events[-1][0],3600)
        self.assertTrue(all(e.traffic_flow==0 for _,e in timed_events(replace(o,traffic_flow=0))))

    def test_free_start_and_midnight_crossing(self):
        periods=source_period('2025-01-01T23:00',2)
        self.assertEqual([(d.day,d.hour) for d in periods],[(1,23),(2,0)])
        rows=read_period(ROOT/'data/processed_traffic_flow.csv','2025-01-01T23:00',2)
        self.assertEqual({o.time_slot for o in rows},{'23:00-24:00','00:00-01:00'})
        with self.assertRaises(ValueError): source_period('2025-01-01T08:30',1)
        with self.assertRaises(ValueError): read_period(ROOT/'data/processed_traffic_flow.csv','2099-01-01T14:00',1)

    def test_partial_stream_is_excluded_from_scenario_history(self):
        from federation.fiware.history import TimescaleHistory
        from federation.fiware.text_codec import encode_road_name
        o=observation()
        base=dict(entity_id=o.entity_id,trafficflow=150,edgeid=o.edge_id,taz=o.taz,sourceid=o.source_id,
            roadnamehex=encode_road_name(o.road_name),hourstart=o.observed_at,hoursampleid=o.sample_id)
        rows=[(dict(base,sampleid='partial',hourcomplete=False,trafficflow=4,observedat='2025-01-01T13:01:00Z'),),
              (dict(base,sampleid='final',hourcomplete=True,observedat='2025-01-01T13:59:59.999Z'),)]
        db=MagicMock();db.execute.return_value.fetchone.return_value=('table',)
        db.execute.return_value.fetchall.return_value=rows
        with patch('psycopg.connect') as connect:
            connect.return_value.__enter__.return_value=db
            result=TimescaleHistory('monolithic',o.taz,'gw_test').query('2025-01-01T13:00:00Z','2025-01-01T14:00:00Z')
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['sample_id'],o.sample_id)
        self.assertEqual(result[0]['observed_at'],o.observed_at)
        self.assertEqual(result[0]['traffic_flow'],150)

    def test_timed_replay_emits_incremental_readings_outside_training_hours(self):
        from federation.fiware.emulator import replay
        sent=[]
        class Client:
            def __init__(self,*a): pass
            def wait_ready(self): pass
            def provision(self,*a): pass
            def send(self,o): sent.append(o);return .1
        def history(*a):
            self.assertTrue(sent[-1].hour_complete)
            self.assertEqual(sent[-1].traffic_flow,150)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.csv'
            with source.open('w',newline='') as handle:
                writer=csv.DictWriter(handle,delimiter=';',fieldnames=['data','edge_id','codZone','ID_univoco_stazione_spira','14:00-15:00'])
                writer.writeheader();writer.writerow({'data':'2025-01-01','edge_id':'23277216#5','codZone':'B','ID_univoco_stazione_spira':'126','14:00-15:00':150})
            with patch('federation.fiware.emulator.time.sleep'):
                manifest=replay('monolithic',source,None,None,session_dir=root/'replay',client_factory=Client,
                    history_reader=history,start_time='2025-01-01T14:00',temporal=True,speed=60)
            data=json.loads(manifest.read_text())
            self.assertEqual(data['status'],'verified')
            self.assertEqual(len(sent),60)
            self.assertEqual(sum(o.flow_increment for o in sent),150)
            self.assertEqual(data['periods'],[{'day':'2025-01-01','hour':14}])


class GuiTests(unittest.TestCase):
    def test_gui_option_reaches_plain_and_rl_simulators(self):
        from federation.fiware.scenarios import _run_scenario
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scenario=root/'scenario';scenario.mkdir()
            routes=scenario/'routes.rou.xml';routes.write_text('<routes/>')
            (scenario/'scenario.json').write_text(json.dumps(dict(status='ready',profile='200_weekdays_8_17',
                day='2025-01-01',hour=8,demand_noise=1.,vehicle_count=1500,seed=42,
                routes_sha256=digest(routes),network_sha256=digest(VENDOR/'sumoenv/static/full.net.xml'))))
            with patch('federation.fiware.scenarios.subprocess.run',return_value=subprocess.CompletedProcess([],0)) as run:
                _run_scenario(scenario,'monolithic','none',root/'plain',gui=True,gui_delay=35)
                command=run.call_args.args[0]
                self.assertEqual(command[0],'sumo-gui')
                self.assertIn('--start',command);self.assertIn('--quit-on-end',command)
                self.assertEqual(command[command.index('--delay')+1],'35')
                _run_scenario(scenario,'federated','local',root/'rl',gui=True,gui_delay=35)
                command=run.call_args.args[0]
                self.assertIn('--gui',command)
                self.assertEqual(command[command.index('--gui-delay')+1],'35')


class ReportTests(unittest.TestCase):
    def test_cpu_deltas_docker_units_and_exported_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            process=dict(pid=10,create_time=1,rss=100*1024**2,uss=80*1024**2,cpu_seconds=1,read_bytes=10,write_bytes=5)
            samples=[dict(wall_ns=1_000_000_000,processes=[process]),
                dict(wall_ns=3_000_000_000,processes=[dict(process,cpu_seconds=3,read_bytes=20,write_bytes=8)])]
            (root/'resources.json').write_text(json.dumps(dict(available=True,samples=samples)))
            (root/'fiware-resources.json').write_text(json.dumps(dict(samples=[dict(wall_ns=2_000_000_000,returncode=0,
                docker_stats=json.dumps([{'Name':'orion','CPUPerc':'25.5%','MemUsage':'1GiB / 2GiB'}]))])))
            (root/'manifest.json').write_text(json.dumps(dict(mode='federated',status='verified',pacing={'speed':60})))
            (root/'ingestion.json').write_text(json.dumps([dict(iot_accept_ms=1,taz='B'),dict(iot_accept_ms=5,taz='B')]))
            series=resource_series(root)
            self.assertEqual([r for r in series if r['scope']=='application'][-1]['cpu_percent'],100)
            self.assertEqual(next(r for r in series if r['scope']=='fiware')['memory_mib'],1024)
            self.assertEqual(bytes_value('2 MB'),2e6)
            out=export_report(root)
            info=json.loads((out/'summary.json').read_text())
            self.assertEqual(info['mode'],'federated')
            self.assertEqual(info['latency_ms']['iot_accept_ms']['p95'],5)
            with zipfile.ZipFile(out/'metrics-export.zip') as archive:
                self.assertTrue({'resources.csv','latencies.csv','summary.json','footprint.png','footprint.svg','latency.png','latency.svg'}<=set(archive.namelist()))
            self.assertIn('federated',(out/'resources.csv').read_text())
