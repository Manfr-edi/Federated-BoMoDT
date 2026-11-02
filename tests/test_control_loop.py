"""Control events must cross the broker, preserve identity and fail closed."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import time
from unittest.mock import patch
from unittest.mock import MagicMock
from urllib.parse import parse_qs,urlsplit,unquote
from federation.fiware.control import Endpoint, ControlBus, ENTITY_TYPE


class MemoryTransport:
    def __init__(self): self.entities={};self.posts=[]
    def __call__(self,method,url,body=None,headers=None,**kwargs):
        if method=='POST':
            self.posts.append((url,body))
            if '/iot/json?' in url:
                device=parse_qs(urlsplit(url).query)['i'][0]
                self.entities[device]=dict(body)
            return None
        device=unquote(urlsplit(url).path).rsplit(':',1)[-1]
        return self.entities.get(device,{})


class ControlTests(unittest.TestCase):
    def test_grafana_probe_requires_the_matching_observation_payload(self):
        from federation.fiware.control import encode
        with tempfile.TemporaryDirectory() as tmp:
            payload=dict(observation=[.5],generated_ns=time.perf_counter_ns()-1_000_000)
            wire,_=encode(payload)
            calls=[]
            def query(method,url,body=None,**kwargs):
                calls.append(url)
                event='wrong-event' if len(calls)==1 else 'gw_control_test:B:observation:0'
                return {'results':{'A':{'frames':[{'schema':{'fields':[{'name':'eventid'},{'name':'payloadhex'}]},
                    'data':{'values':[[event],[wire]]}}]}}}
            node=Endpoint(self.spec(tmp),'B',transport=query)
            with patch('federation.fiware.control.time.sleep'): node.probe_grafana(0,payload)
            rows=[json.loads(l) for l in node.trace.read_text().splitlines()]
            self.assertEqual(len(calls),2)
            self.assertTrue(all(url.endswith('/api/ds/query') for url in calls))
            self.assertEqual(rows[0]['metric'],'observation_to_grafana_query_ms')
            self.assertEqual(rows[0]['poll_count'],2)
            self.assertGreaterEqual(rows[0]['value_ms'],1)
            self.assertEqual(rows[0]['generated_ns'],payload['generated_ns'])

    def spec(self,root,mode='monolithic'):
        return dict(mode=mode,output=str(root),fiware_tenant='gw_control_test',
            fiware_start='2025-01-01T07:00:00Z',step_seconds=600,timeout=0)

    def test_roundtrip_and_integrity_and_future_barrier(self):
        with tempfile.TemporaryDirectory() as tmp:
            wire=MemoryTransport();node=Endpoint(self.spec(tmp),'B',transport=wire)
            payload=dict(observation=[.123,2.],mask=[True,False],label="Via de' Carracci")
            ref=node.publish('observation',2,payload)
            self.assertEqual(node.read('observation',2,ref['sha256']),payload)
            self.assertEqual(wire.posts[-1][1]['simulationSeconds'],1200)
            self.assertEqual(wire.posts[-1][1]['observedAt'],'2025-01-01T07:20:00+00:00')
            with self.assertRaisesRegex(ValueError,'advanced'): node.read('observation',1)
            with self.assertRaisesRegex(TimeoutError,'TAZ=B'): node.read('observation',3)
            wire.entities[node.device('observation')]['payloadHex']='7b7d'
            with self.assertRaisesRegex(ValueError,'integrity'): node.read('observation',2,ref['sha256'])

    def test_same_sensor_payloads_with_different_stack_placement(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ['monolithic','federated']:
                wire=MemoryTransport();bus=ControlBus(self.spec(Path(tmp)/mode,mode),['F','B'],provision=False)
                try:
                    for node in bus.nodes: node.transport=wire
                    payloads=[{'observation':[1.]},{'observation':[2.]}]
                    self.assertEqual(bus.exchange('observation',0,payloads),payloads)
                    urls=[urlsplit(url).netloc for url,_ in wire.posts]
                    self.assertEqual(len(set(urls)),1 if mode=='monolithic' else 2)
                finally: bus.close()

    def test_local_worker_fetches_state_and_publishes_command(self):
        from federation.node import Twin
        with tempfile.TemporaryDirectory() as tmp:
            spec=dict(self.spec(tmp),taz='B',port=0,demo=True,delay_ms=0)
            wire=MemoryTransport();node=Endpoint(spec,'B',transport=wire)
            node.publish('observation',0,dict(observation=[.9],mask=[True]))
            node.publish('coordination',0,dict(price=0.,price_context=[]))
            with patch('federation.fiware.control.Endpoint',return_value=node): twin=Twin(spec)
            try:
                # No observation/action context is supplied through the local RPC.
                event=twin.handle({'op':'observe','event':{'taz':'B','seq':0}})
                command=node.read('command',0,event['fiware_command']['sha256'])
                self.assertEqual(command['action'],[2])
                self.assertTrue(command['created_ns']>0)
                self.assertEqual(event['observation'],[.9])
            finally: twin.close()

    def test_provisioning_uses_iot_agent_and_historical_subscription(self):
        with tempfile.TemporaryDirectory() as tmp:
            wire=MemoryTransport();bus=ControlBus(self.spec(tmp),['B'],provision=False)
            node=bus.nodes[0];node.transport=wire;node.client.transport=wire
            with patch.object(node.client,'wait_ready'): bus._provision_stack(node)
            node.provision_devices()
            bus.close()
            subscription=next(body for url,body in wire.posts if url.endswith('/v2/subscriptions'))
            self.assertEqual(subscription['subject']['entities'][0]['type'],ENTITY_TYPE)
            self.assertIn('payloadHex',subscription['notification']['attrs'])
            devices=wire.posts[-1][1]['devices']
            self.assertEqual(len(devices),4)

    def test_missing_history_retries_same_logical_event_through_iot(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec=self.spec(tmp);spec['timeout']=2
            wire=MemoryTransport();bus=ControlBus(spec,['B'],provision=False)
            node=bus.nodes[0];node.transport=wire
            ref=node.publish('observation',0,{'observation':[.5]})
            db=MagicMock()
            db.execute.return_value.fetchone.return_value=('table',)
            db.execute.return_value.fetchall.side_effect=lambda:[] if len(wire.posts)<2 else [
                (node.event_id('observation',0),wire.entities[node.device('observation')]['payloadHex'])]
            with patch('psycopg.connect') as connect:
                connect.return_value.__enter__.return_value=db
                try: bus.verify_history(0)
                finally: bus.close()
            self.assertEqual(len(wire.posts),2)
            a,b=[body for _,body in wire.posts]
            self.assertEqual(a['eventId'],b['eventId'])
            self.assertEqual(a['payloadHex'],b['payloadHex'])
            self.assertNotEqual(a['deliveryId'],b['deliveryId'])
            self.assertEqual(bus.last_verified,0)
            self.assertEqual(node.read('observation',0,ref['sha256']),{'observation':[.5]})

    def test_whole_operation_totals_include_both_scopes_and_raw_transport(self):
        from federation.fiware.reports import export_report
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'scenario-run.json').write_text(json.dumps(dict(mode='monolithic',integrated=True,step_seconds=600)))
            process=dict(pid=1,create_time=1,rss=100,uss=80,cpu_seconds=1,read_bytes=0,write_bytes=0)
            (root/'operation-resources.json').write_text(json.dumps(dict(samples=[
                dict(wall_ns=1_000_000_000,processes=[process]),
                dict(wall_ns=3_000_000_000,processes=[dict(process,cpu_seconds=3,read_bytes=30,write_bytes=50)])])))
            (root/'resources.json').write_text(json.dumps(dict(samples=[])))
            samples=[]
            for when,network in [(1_000_000_000,'1kB / 2kB'),(3_000_000_000,'4kB / 8kB')]:
                samples.append(dict(wall_ns=when,returncode=0,docker_stats=json.dumps([dict(Name='orion',CPUPerc='50%',MemUsage='1MiB / 2MiB',NetIO=network,BlockIO='0B / 0B')])))
            (root/'fiware-resources.json').write_text(json.dumps(dict(samples=samples)))
            traces=root/'fiware-control';traces.mkdir()
            (traces/'world-B.jsonl').write_text('\n'.join(json.dumps(dict(publication=True,event_id='same',payload_bytes=5,metric='observation_iot_accept_ms',value_ms=1,seq=0,taz='B')) for _ in range(2)))
            out=export_report(root)
            report=json.loads((out/'summary.json').read_text())
            self.assertEqual(report['accounted_cpu_seconds'],3)
            self.assertEqual(report['resource_totals']['fiware']['network_rx_bytes'],3000)
            self.assertEqual(report['resource_totals']['fiware']['network_tx_bytes'],6000)
            self.assertEqual(report['resource_totals']['application']['write_bytes'],50)
            self.assertEqual(report['control_transport']['logical_events'],1)
            self.assertEqual(report['control_transport']['publication_attempts'],2)
            self.assertEqual(report['latency_ms']['observation_iot_accept_ms']['count'],2)
            with zipfile.ZipFile(out/'metrics-export.zip') as archive:
                self.assertIn('raw/operation-resources.json',archive.namelist())
                self.assertIn('raw/fiware-control/world-B.jsonl',archive.namelist())

    def test_dashboard_exposes_persisted_simulation_state_in_both_modes(self):
        from streamlit.testing.v1 import AppTest
        from federation.fiware import ui
        from federation.models import ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            for mode in ['monolithic','federated']:
                folder=root/'experiments'/mode;folder.mkdir(parents=True)
                (folder/'control-run.json').write_text(json.dumps(dict(mode=mode,tenant='gw_control_test',
                    status='completed',step_seconds=600,source_start='2025-01-01T07:00:00Z')))
                with patch.object(ui,'ROOT',root):
                    app=AppTest.from_file(str(ROOT/'dashboards'/f'{mode}.py'),default_timeout=20).run()
                    next(r for r in app.radio if r.label=='FIWARE data').set_value('Simulation control').run()
                    self.assertFalse(app.exception)
                    self.assertTrue(any(s.label=='Simulation run' for s in app.selectbox))
