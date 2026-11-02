"""Synchronous FIWARE control barriers, with no local-state fallback.

All payloads travel through IoT Agent -> Orion -> QuantumLeap/Timescale.
Consumers read Orion; history is verified before physics advances. Hex encodes
structured values losslessly for the restricted Text parser of Orion-LD 1.5.1.
Each run owns a tenant, and each TAZ/kind owns one current-state entity.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import threading
from urllib.parse import quote, urlencode
import uuid
from .client import FiwareClient, FiwareHTTPError, request
from .deployment import service_key

KINDS=('observation','coordination','command','feedback')
ENTITY_TYPE='TwinControlEvent'


def encode(payload):
    raw=json.dumps(payload,separators=(',',':'),allow_nan=False).encode()
    return raw.hex(),hashlib.sha256(raw).hexdigest()


class Endpoint:
    def __init__(self, spec, taz, role='world', transport=request):
        self.spec,self.taz,self.role=spec,taz,role
        self.client=FiwareClient(spec['mode'],taz,spec['fiware_tenant'],transport)
        self.transport=transport
        self.trace=Path(spec['output'])/'fiware-control'/f'{role}-{taz}.jsonl'
        self.trace.parent.mkdir(parents=True,exist_ok=True)
        self.trace_lock=threading.Lock()

    def device(self,kind):
        if kind not in KINDS: raise ValueError('Unknown control event kind')
        return f'control_{self.taz}_{kind}'

    def event_id(self,kind,seq): return f'{self.client.tenant}:{self.taz}:{kind}:{seq}'

    def log(self,**row):
        row.update(taz=self.taz,role=self.role,wall_ns=time.perf_counter_ns())
        with self.trace_lock,self.trace.open('a') as handle: handle.write(json.dumps(row)+'\n')

    def provision_devices(self):
        attrs=[dict(object_id=k,name=k,type=t) for k,t in [
            ('payloadHex','Text'),('eventId','Text'),('deliveryId','Text'),
            ('observedAt','DateTime'),('sequence','Number'),('simulationSeconds','Number')]]
        devices=[dict(device_id=self.device(k),entity_name=f'urn:ngsi-ld:{ENTITY_TYPE}:{self.device(k)}',
            entity_type=ENTITY_TYPE,protocol='PDI-IoTA-JSON',transport='HTTP',attributes=attrs,
            static_attributes=[dict(name='taz',type='Text',value=self.taz),dict(name='eventKind',type='Text',value=k)]) for k in KINDS]
        self.transport('POST',self.client.urls['north']+'/iot/devices',{'devices':devices},self.client.headers)

    def publish(self,kind,seq,payload):
        wire,sha=encode(payload)
        stamp=datetime.fromisoformat(self.spec['fiware_start'].replace('Z','+00:00'))+timedelta(seconds=seq*self.spec.get('step_seconds',600))
        body=dict(payloadHex=wire,eventId=self.event_id(kind,seq),deliveryId=uuid.uuid4().hex,
                  observedAt=stamp.isoformat(),sequence=seq,simulationSeconds=seq*self.spec.get('step_seconds',600))
        started=time.perf_counter_ns()
        self.transport('POST',self.client.urls['south']+'/iot/json?'+urlencode({'i':self.device(kind),'k':self.client.key}),body)
        self.log(metric=kind+'_iot_accept_ms',value_ms=(time.perf_counter_ns()-started)/1e6,
                 seq=seq,event_id=body['eventId'],payload_sha256=sha,payload_bytes=len(json.dumps(body).encode()),publication=True)
        return dict(kind=kind,seq=seq,sha256=sha,published_ns=started)

    def read(self,kind,seq,sha256=None):
        started=time.perf_counter_ns();deadline=time.monotonic()+self.spec.get('timeout',120)
        entity=quote(f'urn:ngsi-ld:{ENTITY_TYPE}:{self.device(kind)}',safe='')
        while True:
            try:
                row=self.transport('GET',self.client.urls['broker']+'/v2/entities/'+entity+'?options=keyValues',headers=self.client.headers,
                    timeout=min(10,max(.01,deadline-time.monotonic())))
            except FiwareHTTPError as error:
                if error.status!=404: raise
                row={}
            if row.get('eventId')==self.event_id(kind,seq):
                raw=bytes.fromhex(row['payloadHex'])
                if sha256 and hashlib.sha256(raw).hexdigest()!=sha256: raise ValueError('FIWARE control payload integrity mismatch')
                payload=json.loads(raw)
                self.log(metric=self.role+'_'+kind+'_broker_read_ms',value_ms=(time.perf_counter_ns()-started)/1e6,seq=seq)
                return payload
            if row.get('sequence',-1)>seq: raise ValueError('FIWARE control sequence advanced past the requested barrier')
            if time.monotonic()>=deadline: raise TimeoutError(f'FIWARE {kind} barrier timed out: TAZ={self.taz}, seq={seq}')
            time.sleep(.02)

    def exchange(self,kind,seq,payload):
        started=time.perf_counter_ns()
        ref=self.publish(kind,seq,payload)
        result=self.read(kind,seq,ref['sha256'])
        self.log(metric=kind+'_publish_to_read_ms',value_ms=(time.perf_counter_ns()-started)/1e6,seq=seq)
        return result

    def probe_grafana(self,seq,payload):
        """Observe an actual datasource response, not a browser-render event."""
        started=time.perf_counter_ns()
        deadline=time.monotonic()+max(0,15-(started-payload['generated_ns'])/1e9);polls=0
        identity=self.event_id('observation',seq)
        _,expected=encode(payload)
        # Tenant/TAZ/kind are validated identifiers, and seq is an internal int.
        query=f"SELECT eventid, payloadhex FROM mt{self.client.tenant}.ettwincontrolevent WHERE eventid='{identity}' AND sequence={int(seq)}"
        body={'from':'0','to':'now','queries':[dict(refId='A',datasource={'uid':'traffic-history','type':'postgres'},
            rawSql=query,format='table',rawQuery=True,intervalMs=1000,maxDataPoints=100)]}
        error=None
        while time.monotonic()<deadline:
            polls+=1
            try:
                response=self.transport('POST',self.client.urls['grafana']+'/api/ds/query',body,timeout=5)
                result=response.get('results',{}).get('A',{})
                error=result.get('error')
                for frame in result.get('frames',[]):
                    fields=[f['name'] for f in frame.get('schema',{}).get('fields',[])]
                    values=frame.get('data',{}).get('values',[])
                    if 'eventid' not in fields or 'payloadhex' not in fields: continue
                    for event,wire in zip(values[fields.index('eventid')],values[fields.index('payloadhex')]):
                        if event==identity and hashlib.sha256(bytes.fromhex(wire)).hexdigest()==expected:
                            completed=time.perf_counter_ns()
                            self.log(metric='observation_to_grafana_query_ms',value_ms=(completed-payload['generated_ns'])/1e6,
                                seq=seq,event_id=identity,poll_count=polls,generated_ns=payload['generated_ns'],
                                grafana_response_ns=completed,observer_wait_ms=(completed-started)/1e6)
                            return
            except Exception as exc: error=str(exc)
            time.sleep(.25)
        self.log(metric='grafana_probe_unavailable',seq=seq,event_id=identity,error=error or 'Matching row not returned within 15 seconds',poll_count=polls)


class ControlBus:
    def __init__(self,spec,taz_ids,provision=True):
        self.spec,self.ids=spec,list(taz_ids)
        self.nodes=[Endpoint(spec,t) for t in self.ids]
        self.pool=ThreadPoolExecutor(max_workers=len(self.ids))
        self.observer_pool=ThreadPoolExecutor(max_workers=len(self.ids))
        self.persisted_confirmed_ns={}
        self.observation_sources={}
        self.last_verified=-1
        self.status_path=Path(spec['output'])/'control-run.json'
        self.state=dict(mode=spec['mode'],tenant=spec['fiware_tenant'],source_start=spec['fiware_start'],
            step_seconds=spec.get('step_seconds',600),taz_ids=self.ids,status='provisioning',
            control_protocol_version=2,grafana_probe='datasource query',grafana_probe_interval_ms=250)
        self._status()
        try:
            if provision:
                owners=self.nodes[:1] if spec['mode']=='monolithic' else self.nodes
                list(self.pool.map(self._provision_stack,owners))
                list(self.pool.map(lambda n:n.provision_devices(),self.nodes))
        except BaseException:
            self.close();raise

    def _provision_stack(self,node):
        c=node.client;c.wait_ready(timeout=self.spec.get('timeout',120))
        self.nodes[0].log(metric='stack_provision_start',owner=node.taz)
        c.transport('POST',c.urls['north']+'/iot/services',{'services':[dict(apikey=c.key,
            cbroker=f'http://{service_key(c.mode,c.taz,"orion")}:1026',entity_type=ENTITY_TYPE,resource='/iot/json')]},c.headers)
        c.transport('POST',c.urls['broker']+'/v2/subscriptions',{
            'description':'Persist simulated control events',
            'subject':{'entities':[{'idPattern':'.*','type':ENTITY_TYPE}],'condition':{'attrs':['deliveryId']}},
            'notification':{'httpCustom':{'url':f'http://{service_key(c.mode,c.taz,"quantumleap")}:8668/v2/notify',
                'headers':{**c.headers,'Fiware-TimeIndex-Attribute':'observedAt'}},
                'attrs':['payloadHex','eventId','observedAt','sequence','simulationSeconds','taz','eventKind']}
            },c.headers)

    def exchange(self,kind,seq,payloads):
        if len(payloads)!=len(self.nodes): raise ValueError('Every TAZ must participate in the control barrier')
        # The same I/O concurrency in both architectures avoids manufacturing a
        # monolithic bottleneck by serializing otherwise independent requests.
        self.state.update(status='running',stage=kind,seq=seq,simulation_seconds=seq*self.spec.get('step_seconds',600));self._status()
        received=list(self.pool.map(lambda pair:pair[0].exchange(kind,seq,pair[1]),zip(self.nodes,payloads)))
        if kind=='observation':
            for node,payload in zip(self.nodes,payloads):
                if payload.get('generated_ns') is not None:
                    self.observation_sources[node.taz]=payload['generated_ns']
                    self.observer_pool.submit(node.probe_grafana,seq,payload)
        return received

    def read_commands(self,seq,events):
        return list(self.pool.map(lambda pair:pair[0].read('command',seq,pair[1]['fiware_command']['sha256']),zip(self.nodes,events)))

    def verify_history(self,seq,stage='control'):
        """Verify every published payload for this barrier, including worker commands."""
        expected={}
        for path in (Path(self.spec['output'])/'fiware-control').glob('*.jsonl'):
            for line in path.read_text().splitlines():
                r=json.loads(line)
                if r.get('publication') and r.get('seq')==seq: expected[r['event_id']]=r['payload_sha256']
        start=time.perf_counter_ns();deadline=time.monotonic()+self.spec.get('timeout',120)
        repairs=0;next_repair=time.monotonic()+.2
        owners=self.nodes[:1] if self.spec['mode']=='monolithic' else self.nodes
        def query(node):
            import os
            import psycopg
            from psycopg import sql
            with psycopg.connect(host='127.0.0.1',port=node.client.urls['postgres_port'],dbname='quantumleap',
                user='postgres',password=os.environ.get('BOMODT_DB_PASSWORD','research'),connect_timeout=5,
                options='-c statement_timeout=5000') as db:
                schema='mt'+node.client.tenant
                if db.execute('SELECT to_regclass(%s)',(schema+'.ettwincontrolevent',)).fetchone()[0] is None: return [],time.perf_counter_ns()
                rows=db.execute(sql.SQL('SELECT eventid,payloadhex FROM {}.ettwincontrolevent WHERE sequence=%s AND eventid IS NOT NULL').format(sql.Identifier(schema)),(seq,)).fetchall()
                return rows,time.perf_counter_ns()
        while True:
            found={}
            for rows,confirmed_ns in self.pool.map(query,owners):
                for identity,wire in rows:
                    if identity in expected:
                        sha=hashlib.sha256(bytes.fromhex(wire)).hexdigest()
                        if sha!=expected[identity]: raise ValueError('Conflicting FIWARE control history')
                        found[identity]=sha
                        self.persisted_confirmed_ns.setdefault(identity,confirmed_ns)
            if found==expected:
                self.last_verified=seq
                self.state.update(verified_sequence=seq);self._status()
                self.nodes[0].log(metric=stage+'_history_barrier_ms',value_ms=(time.perf_counter_ns()-start)/1e6,seq=seq,event_count=len(expected),repair_batches=repairs)
                return
            if time.monotonic()>=deadline: raise TimeoutError(f'Control history incomplete at seq={seq}: {len(found)}/{len(expected)} events')
            if repairs<3 and time.monotonic()>=next_repair:
                def repair(identity):
                    _,taz,kind,_=identity.split(':')
                    node=self.nodes[self.ids.index(taz)]
                    payload=node.read(kind,seq,expected[identity])
                    # Replay the same logical event with a fresh delivery nonce;
                    # do not fabricate new sensor data or bypass IoT Agent.
                    node.publish(kind,seq,payload)
                list(self.pool.map(repair,set(expected)-set(found)))
                repairs+=1;next_repair=time.monotonic()+.2
            time.sleep(.05)

    def _status(self):
        self.status_path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.status_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state,indent=2));temporary.replace(self.status_path)

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=True)
        self.observer_pool.shutdown(wait=True,cancel_futures=False)
        self.state['status']='completed' if self.last_verified==self.spec.get('steps',6) else 'failed'
        self._status()
