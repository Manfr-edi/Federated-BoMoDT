"""Offline contract tests plus real Streamlit rendering; Docker integration is opt-in."""
from datetime import datetime
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from federation.models import ROOT, VENDOR
from federation.fiware.deployment import generate, endpoints, TAZS
from federation.fiware.observations import read_observations, time_window, digest
from federation.fiware.client import FiwareClient
from federation.fiware.history import read_session
from federation.fiware.emulator import replay
from federation.fiware.scenarios import write_edge_counts, training_demand


def fixture():
    return read_observations(ROOT/'data/processed_traffic_flow.csv','2025-01-01',[8])


class DeploymentTests(unittest.TestCase):
    def test_startup_replaces_stale_commands_without_changing_volumes(self):
        from federation.fiware import deployment
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'configs').mkdir()
            shutil.copyfile(ROOT/'configs/fiware-images.json',root/'configs/fiware-images.json')
            with patch.object(deployment,'ROOT',root):
                path=generate('monolithic')
                previous=json.loads(path.read_text())
                previous['services']['city-orion']['command'].append('-multiservice')
                path.write_text(json.dumps(previous))
                command=deployment.compose_command('monolithic','up')
                current=json.loads(path.read_text())
                self.assertNotIn('-multiservice',current['services']['city-orion']['command'])
                self.assertEqual(previous['volumes'],current['volumes'])
                self.assertEqual(command[-2:],['up','-d'])
                self.assertIn('--all',deployment.compose_command('monolithic','ps'))

    def test_isolation_and_compose_validation(self):
        for mode, count in [('monolithic',7),('federated',77)]:
            with tempfile.TemporaryDirectory() as tmp:
                path=generate(mode,tmp);doc=json.loads(path.read_text())
                self.assertEqual(len(doc['services']),count)
                for name,service in doc['services'].items():
                    if name.endswith('-orion'):
                        # Official 1.5.1 Dockerfile-ubi supplies these flags itself.
                        entrypoint=['orionld','-fg','-multiservice','-ngsiv1Autocast']
                        self.assertNotIn('entrypoint',service)
                        effective=entrypoint+service['command']
                        for option in ('-fg','-multiservice','-ngsiv1Autocast'):
                            self.assertEqual(effective.count(option),1)
                        self.assertIn('/version',service['healthcheck']['test'][-1])
                        self.assertEqual(service['command'][-2:],['-dbPoolSize','64'])
                        self.assertEqual(doc['services'][name.removesuffix('-orion')+'-iot']
                            ['depends_on'][name]['condition'],'service_healthy')
                ports=[]
                for service in doc['services'].values():
                    self.assertNotIn('container_name',service)
                    self.assertEqual(len(service['networks']),1)
                    ports.extend(service.get('ports',[]))
                self.assertEqual(len(ports),len(set(ports)))
                if shutil.which('docker'):
                    subprocess.run(['docker','compose','-f',str(path),'config','--quiet'],check=True)
        self.assertTrue(set(endpoints('monolithic','F').values()).isdisjoint(endpoints('federated','F').values()))


class DataPathTests(unittest.TestCase):
    def test_socket_timeouts_identify_endpoint_without_exposing_keys(self):
        from federation.fiware.client import request, FiwareConnectionError
        for error in (TimeoutError('timed out'), ConnectionResetError('reset')):
            with patch('federation.fiware.client.urlopen',side_effect=error):
                with self.assertRaises(FiwareConnectionError) as caught:
                    request('POST','http://127.0.0.1:12000/v2/subscriptions?k=private-key',{})
            self.assertIn('POST',str(caught.exception))
            self.assertIn('/v2/subscriptions',str(caught.exception))
            self.assertNotIn('private-key',str(caught.exception))

    def test_readiness_checks_database_even_when_version_is_healthy(self):
        from federation.fiware.client import FiwareConnectionError
        def transport(method,url,**kwargs):
            if '/v2/subscriptions' in url:
                self.assertEqual(kwargs['headers']['Fiware-Service'],'gw_test')
                raise FiwareConnectionError('database pool exhausted')
            return {}
        client=FiwareClient('federated','B','gw_test',transport)
        self.assertEqual(client.health()['broker'],'ready')
        self.assertIn('exhausted',client.health()['broker_subscriptions'])
        with self.assertRaisesRegex(TimeoutError,'broker_subscriptions'):
            client.wait_ready(timeout=0)

    def test_reported_carracci_device_round_trips_through_wire_encoding(self):
        from federation.fiware.text_codec import decode_road_name
        observation=next(o for o in fixture() if o.device_id=='loop_97cb3720a353e2f7152d')
        self.assertEqual(observation.road_name,"VIA DE' CARRACCI")
        calls=[]
        client=FiwareClient('federated','B','gw_test',lambda *args,**kwargs:calls.append(args))
        client.provision([observation])
        device=calls[-1][2]['devices'][0]
        road=next(a for a in device['static_attributes'] if a['name']=='roadNameHex')
        self.assertEqual(road['type'],'Text')
        self.assertEqual(decode_road_name({'roadNameHex':road['value']}),observation.road_name)
        self.assertEqual(device['entity_name'],observation.entity_id)

    def test_source_road_names_survive_restricted_text_validation(self):
        from federation.fiware.text_codec import decode_road_name
        forbidden=set("<>\"'=;()")
        devices=[]
        def strict_transport(method,url,body=None,headers=None):
            if url.endswith('/iot/devices'):
                device=body['devices'][0]
                for attribute in device['static_attributes']:
                    if attribute['type']=='Text':
                        self.assertFalse(forbidden.intersection(attribute['value']),attribute['name'])
                devices.append(device)
        observations=fixture()
        client=FiwareClient('monolithic','F','gw_test',strict_transport)
        client.provision(observations)
        self.assertEqual(len(devices),len(observations))
        self.assertTrue(any("'" in o.road_name for o in observations))
        for observation,device in zip(observations,devices):
            road=next(a for a in device['static_attributes'] if a['name']=='roadNameHex')
            self.assertEqual(decode_road_name({'roadNameHex':road['value']}),observation.road_name)

    def test_human_labels_decode_for_broker_and_sql_views(self):
        from federation.fiware.text_codec import encode_road_name, decode_road_name
        for value in ("VIA DE' CARRACCI",'Via città (est); "A" <B> = C',''):
            encoded=encode_road_name(value)
            self.assertEqual(decode_road_name({'roadNameHex':encoded}),value)
            self.assertEqual(decode_road_name({'roadnamehex':encoded}),value)
        value="VIA D'AZEGLIO"
        client=FiwareClient('federated','L','gw_test',lambda *a,**k:[{'roadNameHex':encode_road_name(value)}])
        self.assertEqual(client.entities(),[{'roadName':value}])

    def test_measurement_http_failure_identifies_sample_and_preserves_status(self):
        from federation.fiware.client import FiwareHTTPError
        observation=fixture()[0]
        def failure(*args,**kwargs):
            raise FiwareHTTPError(400,'{"name":"ENTITY_GENERIC_ERROR"}')
        client=FiwareClient('federated',observation.taz,'gw_test',failure)
        with self.assertRaises(FiwareHTTPError) as caught:
            client.send(observation)
        self.assertEqual(caught.exception.status,400)
        for detail in ('ENTITY_GENERIC_ERROR',observation.device_id,observation.taz,'gw_test',observation.observed_at):
            self.assertIn(detail,str(caught.exception))
        self.assertNotIn(client.key,str(caught.exception))

    def test_retry_changes_delivery_identity_without_changing_source_sample(self):
        payloads=[]
        client=FiwareClient('federated','B','gw_test',lambda *a,**k:payloads.append(a[2]))
        observation=next(o for o in fixture() if o.taz=='B')
        client.send(observation);client.send(observation)
        self.assertNotEqual(payloads[0]['deliveryId'],payloads[1]['deliveryId'])
        for payload in payloads:
            self.assertEqual(payload['sampleId'],observation.sample_id)
            self.assertEqual(payload['observedAt'],observation.observed_at)
            self.assertEqual(payload['trafficFlow'],observation.traffic_flow)

    def test_replay_retries_only_missing_samples_before_next_source_hour(self):
        from collections import Counter
        from federation.fiware.history import IncompleteHistoryError
        missing=fixture()[0].sample_id
        sent=Counter()
        state={'hour8_verified':False}
        class Client:
            def __init__(self,*_): pass
            def wait_ready(self): pass
            def provision(self,*_): pass
            def send(self,observation):
                if observation.time_slot=='17:00-18:00':
                    self_test.assertTrue(state['hour8_verified'])
                sent[observation.sample_id]+=1
                return 1.
        def history(manifest,day,hour):
            if hour==8:
                if sent[missing]<2: raise IncompleteHistoryError({missing},78)
                state['hour8_verified']=True
        self_test=self
        with tempfile.TemporaryDirectory() as tmp:
            path=replay('monolithic',ROOT/'data/processed_traffic_flow.csv','2025-01-01',[8,17],
                verify_timeout=2,session_dir=Path(tmp)/'replay',client_factory=Client,history_reader=history)
            manifest=json.loads(path.read_text())
            self.assertEqual(manifest['status'],'verified')
            self.assertEqual(manifest['completed_hours'],[8,17])
            self.assertEqual(sent[missing],2)
            self.assertTrue(all(count==1 for key,count in sent.items() if key!=missing))
            self.assertEqual(manifest['hourly_verification'][0]['repair_batches'],1)

    def test_bodyless_requests_omit_content_type_and_preserve_tenant(self):
        from federation.fiware.client import request
        headers={'Fiware-Service':'gw_test','Fiware-ServicePath':'/',
                 'cOnTeNt-TyPe':'application/json'}
        for method in ('GET','DELETE'):
            with self.subTest(method=method), patch('federation.fiware.client.urlopen',
                    return_value=io.BytesIO(b'{}')) as opened:
                request(method,'http://orion:1026/v2/entities',headers=headers)
                sent=opened.call_args.args[0]
                wire_headers={key.lower():value for key,value in sent.header_items()}
                self.assertNotIn('content-type',wire_headers)
                self.assertIsNone(sent.data)
                self.assertEqual(wire_headers['accept'],'application/json')
                self.assertEqual(wire_headers['fiware-service'],'gw_test')
                self.assertEqual(wire_headers['fiware-servicepath'],'/')
        self.assertIn('cOnTeNt-TyPe',headers)  # Do not mutate shared client defaults.

    def test_json_write_requests_retain_content_type_and_payload(self):
        from federation.fiware.client import request
        for method in ('POST','PUT','PATCH'):
            for body in ({},{'trafficFlow':12}):
                with self.subTest(method=method,body=body), patch('federation.fiware.client.urlopen',
                        return_value=io.BytesIO(b'')) as opened:
                    request(method,'http://orion:1026/v2/entities',body)
                    sent=opened.call_args.args[0]
                    self.assertEqual(sent.get_header('Content-type'),'application/json')
                    self.assertEqual(json.loads(sent.data),body)

    def test_get_delete_bodies_fail_before_sending(self):
        from federation.fiware.client import request
        with patch('federation.fiware.client.urlopen') as opened:
            for method in ('get','DELETE'):
                with self.assertRaisesRegex(ValueError,'must not include a body'):
                    request(method,'http://orion:1026/v2/entities',{})
            opened.assert_not_called()

    def test_health_and_entity_reads_are_accepted_by_a_strict_transport(self):
        def strict_orion(sent,timeout):
            self.assertEqual(sent.get_method(),'GET')
            self.assertIsNone(sent.data)
            self.assertNotIn('content-type',{key.lower() for key,_ in sent.header_items()})
            return io.BytesIO(b'[]')
        with patch('federation.fiware.client.urlopen',side_effect=strict_orion):
            client=FiwareClient('federated','F','gw_test')
            self.assertEqual(set(client.health().values()),{'ready'})
            self.assertEqual(client.entities(),[])

    def test_connection_failure_names_endpoint_without_device_key(self):
        from urllib.error import URLError
        from federation.fiware.client import request, FiwareConnectionError
        with patch('federation.fiware.client.urlopen',side_effect=URLError(
                ConnectionRefusedError(111,'Connection refused'))):
            with self.assertRaises(FiwareConnectionError) as caught:
                request('POST','http://127.0.0.1:12002/iot/json?i=sensor&k=private-key',{})
        self.assertIn('http://127.0.0.1:12002/iot/json',str(caught.exception))
        self.assertIn('Connection refused',str(caught.exception))
        self.assertNotIn('private-key',str(caught.exception))

    def test_source_time_and_profile_compatibility(self):
        observations=fixture()
        self.assertEqual(observations[0].observed_at,'2025-01-01T07:00:00Z')
        self.assertEqual(len({o.sample_id for o in observations}),len(observations))
        with self.assertRaises(ValueError): time_window('2025-10-26',2)
        with self.assertRaises(ValueError): training_demand('200_weekends_9_20','2025-01-01',9)
        with self.assertRaises(ValueError): training_demand('200_weekdays_8_17','2025-01-01',10)
        self.assertGreater(training_demand('200_weekdays_8_17','2025-01-01',8),0)

    def test_actual_iot_transport_and_timestamp_subscription(self):
        calls=[]
        def transport(*args,**kwargs): calls.append((args,kwargs))
        observation=fixture()[0]
        client=FiwareClient('federated',observation.taz,'gw_test',transport)
        client.provision([observation]);client.send(observation)
        subscription=calls[1][0][2]
        self.assertEqual(subscription['notification']['httpCustom']['headers']['Fiware-TimeIndex-Attribute'],'observedAt')
        self.assertIn('/iot/json?',calls[-1][0][1])
        self.assertEqual(calls[-1][0][2]['observedAt'],observation.observed_at)
        self.assertFalse(any('/v2/entities' in args[1] and args[0]=='POST' for args,_ in calls))
        other=FiwareClient('federated','B' if observation.taz!='B' else 'F','gw_test',transport)
        with self.assertRaises(ValueError): other.send(observation)

    def test_history_completeness_and_identity(self):
        obs=fixture()[:2]
        manifest=dict(mode='federated',tenant='gw_test',observations=[o.to_dict() for o in obs])
        class History:
            rows=[dict(o.to_dict()) for o in obs]
            def __init__(self,mode,taz,tenant): self.taz=taz
            def query(self,*_): return [o for o in self.rows if o['taz']==self.taz]
        self.assertEqual(len(read_session(manifest,'2025-01-01',8,History)),2)
        History.rows=History.rows[:1]
        with self.assertRaisesRegex(ValueError,'incomplete'): read_session(manifest,'2025-01-01',8,History)
        History.rows=[dict(o.to_dict()) for o in obs]
        History.rows[0]['traffic_flow']+=1
        with self.assertRaisesRegex(ValueError,'count differs'): read_session(manifest,'2025-01-01',8,History)

    def test_replay_cannot_mark_success_on_http_acceptance_alone(self):
        class Client:
            def __init__(self,*_): pass
            def wait_ready(self,*_): pass
            def provision(self,*_): pass
            def send(self,*_): return 1.
        def missing(*_): raise ValueError('Not persisted')
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'replay'
            with self.assertRaisesRegex(RuntimeError,'not verified'):
                replay('monolithic',ROOT/'data/processed_traffic_flow.csv','2025-01-01',[8],
                    verify_timeout=0,session_dir=output,client_factory=Client,history_reader=missing)
            self.assertEqual(json.loads((output/'manifest.json').read_text())['status'],'failed')

    def test_edge_count_aggregation_and_invalid_map_rejection(self):
        observation=fixture()[0].to_dict()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'counts.xml'
            metadata=write_edge_counts([observation,observation],path)
            edges=ET.parse(path).getroot().findall('./interval/edge')
            self.assertEqual(len(edges),1)
            self.assertEqual(float(edges[0].get('entered')),observation['traffic_flow'])
            self.assertEqual(metadata['duplicate_edge_rule'],'rounded_mean')
            observation['edge_id']='does-not-exist'
            with self.assertRaises(ValueError): write_edge_counts([observation],path)


class DashboardTests(unittest.TestCase):
    def test_both_control_rooms_render_and_preview(self):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError: self.skipTest('Streamlit is not installed')
        for mode in ['monolithic','federated']:
            app=AppTest.from_file(str(ROOT/'dashboards'/f'{mode}.py'),default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.tabs),6)
            self.assertEqual(app.tabs[-1].label,'Diagnostics')
            self.assertNotIn('Run inspection',[b.label for b in app.tabs[0].button])
            next(n for n in app.number_input if n.label=='Start hour (Europe/Rome)').set_value(14).run()
            self.assertFalse(app.exception)
            self.assertTrue(len(app.dataframe)>0)


@unittest.skipUnless(os.environ.get('BOMODT_LIVE_TEST')=='1','Requires running Docker FIWARE stack')
class LiveFiwareTests(unittest.TestCase):
    def test_emulation_persists_and_can_be_read(self):
        # No mocks: real IoT Agent, Orion-LD, QuantumLeap and Timescale.
        mode=os.environ.get('BOMODT_LIVE_MODE','monolithic')
        path=replay(mode,ROOT/'data/processed_traffic_flow.csv','2025-01-01',[8])
        manifest=json.loads(path.read_text())
        self.assertEqual(manifest['status'],'verified')
        self.assertEqual(len(read_session(manifest,'2025-01-01',8)),len(manifest['observations']))

class ScenarioEngineTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('sumo') and Path('/usr/share/sumo/tools/randomTrips.py').exists(),
                         'SUMO tools are unavailable')
    def test_fiware_history_contract_to_real_routes_and_no_agent_sumo(self):
        try:
            import scipy
            import numpy
        except ImportError: self.skipTest('Route fitting dependencies unavailable')
        from federation.fiware.scenarios import build_scenario, run_scenario
        from federation.fiware.observations import digest
        real_run=subprocess.run
        def captured(*args,**kwargs):
            if not kwargs.get('capture_output'):
                kwargs.setdefault('stdout',subprocess.PIPE)
                kwargs.setdefault('stderr',subprocess.STDOUT)
            result=real_run(*args,**kwargs)
            return result
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); candidates=root/'candidates.rou.xml'
            real_run([os.sys.executable,'/usr/share/sumo/tools/randomTrips.py','-n',
                str(VENDOR/'sumoenv/static/full.net.xml'),'-r',str(candidates),
                '-o',str(root/'trips.xml'),'--seed','42','--end','3600','--period','180','--validate'],
                check=True,capture_output=True)
            edge=ET.parse(candidates).getroot().find('vehicle/route').get('edges').split()[0]
            observation=dict(sample_id='fixture',entity_id='fixture',edge_id=edge,taz='F',source_id='fixture',
                traffic_flow=1.,observed_at=time_window('2025-01-01',8)[0])
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps(dict(status='verified',tenant='gw_fixture',mode='monolithic',
                day='2025-01-01',hours=[8],observations=[observation],source_csv='intentionally-nonexistent.csv',
                network_sha256=digest(VENDOR/'sumoenv/static/full.net.xml'))))
            # Inject only the persistence boundary. Route fitting and SUMO execute for real.
            with patch('federation.fiware.scenarios.subprocess.run',side_effect=captured):
                scenario=build_scenario(manifest,8,'200_weekdays_8_17',root/'scenario',noise=.01,
                    candidates=candidates,history_reader=lambda *_:[observation])
                run_scenario(scenario,'monolithic','none',root/'simulation',integrated=False)
            summary=json.loads((root/'simulation/summary.json').read_text())
            self.assertEqual(summary['status'],'completed')
            self.assertEqual(summary['control'],'none')
            self.assertEqual(summary['network_sha256'],digest(VENDOR/'sumoenv/static/full.net.xml'))


class BackgroundJobTests(unittest.TestCase):
    def test_failed_background_job_records_status_and_log(self):
        import time
        from federation.fiware import jobs
        with tempfile.TemporaryDirectory() as tmp,patch.object(jobs,'JOBS',Path(tmp)):
            jobs.start_job('monolithic','prepare',['prepare','--manifest',str(Path(tmp)/'missing.json'),
                '--hour','8','--profile','200_weekdays_8_17','--output',str(Path(tmp)/'scenario')])
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                items=jobs.list_jobs('monolithic')
                if items and items[0]['status'] not in ('starting','running'): break
                time.sleep(.05)
            self.assertEqual(items[0]['status'],'failed')
            self.assertIn('FileNotFoundError',jobs.tail_log(items[0]))

class PreparedDashboardTests(unittest.TestCase):
    def test_verified_replay_and_prepared_scenario_controls(self):
        try:
            from streamlit.testing.v1 import AppTest
            from federation.fiware import ui
        except ImportError: self.skipTest('Streamlit is not installed')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'configs').symlink_to(ROOT/'configs',target_is_directory=True)
            (root/'data').symlink_to(ROOT/'data',target_is_directory=True)
            scenario=root/'scenarios/prepared';scenario.mkdir(parents=True)
            (scenario/'scenario.json').write_text(json.dumps(dict(status='ready',day='2025-01-01',hour=8,
                profile='200_weekdays_8_17',vehicle_count=1500,actual_route_vehicle_count=1500,
                counts_source='fixture',network_sha256='fixture',demand_noise=1.)))
            for mode in ['monolithic','federated']:
                replay_path=root/'sessions'/mode/'gw_fixture';replay_path.mkdir(parents=True)
                (replay_path/'manifest.json').write_text(json.dumps(dict(status='verified',day='2025-01-01',
                    tenant='gw_fixture',mode=mode,hours=[8],observations=[])))
                with patch.object(ui,'ROOT',root),patch.object(ui,'launch') as launch:
                    app=AppTest.from_file(str(ROOT/'dashboards'/f'{mode}.py'),default_timeout=20).run()
                    self.assertFalse(app.exception)
                    next(c for c in app.checkbox if c.label=='Open SUMO GUI').check().run()
                    button=next(b for b in app.button if b.label=='Start simulation')
                    button.click().run()
                    self.assertFalse(app.exception)
                    self.assertIn('none',launch.call_args[0][2])
                    self.assertIn('--gui',launch.call_args[0][2])
                    self.assertIn(mode,launch.call_args[0][2])
