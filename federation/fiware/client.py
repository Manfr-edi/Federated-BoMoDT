"""FIWARE provisioning and measurement transport; no direct broker-write shortcut."""
import json
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from .deployment import endpoints, service_key, validate_tenant
from .text_codec import encode_road_name, decode_road_name


class FiwareHTTPError(RuntimeError):
    def __init__(self, status, message):
        self.status=status
        self.message=message
        super().__init__(f'FIWARE HTTP {status}: {message}')


class FiwareConnectionError(RuntimeError):
    """Identify the failing service without exposing device keys in query strings."""


def request(method, url, body=None, headers=None, timeout=10):
    method=method.upper()
    if method in ('GET','DELETE') and body is not None:
        raise ValueError(f'FIWARE {method} requests must not include a body')
    payload=json.dumps(body).encode() if body is not None else None
    # Orion rejects Content-Type on bodyless GET/DELETE requests, even when the
    # header came from caller defaults. Accept describes the response instead.
    request_headers={'Accept':'application/json',**(headers or {})}
    if payload is None:
        request_headers={key:value for key,value in request_headers.items() if key.lower()!='content-type'}
    elif not any(key.lower()=='content-type' for key in request_headers):
        request_headers['Content-Type']='application/json'
    req=Request(url,data=payload,method=method,headers=request_headers)
    try:
        with urlopen(req,timeout=timeout) as response:
            raw=response.read()
            return json.loads(raw) if raw else None
    except HTTPError as error:
        raise FiwareHTTPError(error.code,error.read().decode(errors='replace')[:1000]) from error
    except (URLError, TimeoutError, ConnectionError) as error:
        parts=urlsplit(url)
        endpoint=urlunsplit((parts.scheme,parts.netloc,parts.path,'',''))
        raise FiwareConnectionError(
            f'FIWARE {method} failed at {endpoint}: {getattr(error,"reason",error)}. '
            'Check container status and logs in Diagnostics. A responding /version '
            'endpoint alone does not prove that broker database operations work.'
        ) from error


class FiwareClient:
    def __init__(self, mode, taz, tenant, transport=request):
        self.mode,self.taz,self.tenant=mode,taz,validate_tenant(tenant)
        self.urls=endpoints(mode,taz)
        self.headers={'Fiware-Service':tenant,'Fiware-ServicePath':'/'}
        self.transport=transport
        self.key=tenant+'_'+('city' if mode=='monolithic' else taz.lower())

    def health(self):
        checks={}
        for service,path in [('broker','/version'),('north','/iot/about'),('quantumleap','/version')]:
            try:
                self.transport('GET',self.urls[service]+path,timeout=2)
                checks[service]='ready'
            except Exception as error: checks[service]=str(error)
        try:
            # Exercise the database-backed path that provisioning needs. A stuck
            # broker can still answer /version and pass Docker's liveness probe.
            self.transport('GET',self.urls['broker']+'/v2/subscriptions?limit=1',
                           headers=self.headers,timeout=2)
            checks['broker_subscriptions']='ready'
        except Exception as error: checks['broker_subscriptions']=str(error)
        return checks

    def wait_ready(self, timeout=90):
        deadline=time.monotonic()+timeout
        while True:
            status=self.health()
            if all(value=='ready' for value in status.values()): return
            if time.monotonic()>=deadline:
                raise TimeoutError('FIWARE services are not ready: '+str(status))
            time.sleep(min(1,max(0,deadline-time.monotonic())))

    def provision(self, devices):
        broker=service_key(self.mode,self.taz,'orion')
        ql=service_key(self.mode,self.taz,'quantumleap')
        # Fresh tenant per replay makes setup unambiguous and repeatable without deletes.
        self.transport('POST',self.urls['north']+'/iot/services',{'services':[{
            'apikey':self.key,'cbroker':f'http://{broker}:1026','entity_type':'TrafficFlowObserved','resource':'/iot/json'}]},self.headers)
        # Every notification includes static metadata and original observation time.
        self.transport('POST',self.urls['broker']+'/v2/subscriptions',{
            'description':'Persist source-timestamped Bologna measurements',
            'subject':{'entities':[{'idPattern':'.*','type':'TrafficFlowObserved'}],
                       'condition':{'attrs':['sampleId','deliveryId']}},
            'notification':{'httpCustom':{'url':f'http://{ql}:8668/v2/notify',
                'headers':{'Fiware-Service':self.tenant,'Fiware-ServicePath':'/',
                           'Fiware-TimeIndex-Attribute':'observedAt'}},
                'attrs':['trafficFlow','observedAt','sampleId','deliveryId','timeSlot','edgeId','taz','sourceId','roadNameHex','latitude','longitude','flowIncrement','hourSampleId','hourStart','hourComplete'],
                'metadata':['dateCreated','dateModified']}
        },self.headers)
        for observation in devices:
            o=observation
            attrs=[{'object_id':name,'name':name,'type':kind} for name,kind in
                   [('trafficFlow','Number'),('observedAt','DateTime'),('sampleId','Text'),('deliveryId','Text'),('timeSlot','Text'),('flowIncrement','Number'),('hourSampleId','Text'),('hourStart','DateTime'),('hourComplete','Boolean')]]
            static=[{'name':name,'type':kind,'value':value} for name,kind,value in
                    [('edgeId','Text',o.edge_id),('taz','Text',o.taz),('sourceId','Text',o.source_id),
                     # The pinned Orion-LD image rejects apostrophes even with
                     # TextUnrestricted. Encode only this descriptive metadata.
                     ('roadNameHex','Text',encode_road_name(o.road_name)),
                     ('latitude','Number',o.latitude),('longitude','Number',o.longitude)]]
            self.transport('POST',self.urls['north']+'/iot/devices',{'devices':[{
                'device_id':o.device_id,'entity_name':o.entity_id,'entity_type':'TrafficFlowObserved',
                'protocol':'PDI-IoTA-JSON','transport':'HTTP','attributes':attrs,'static_attributes':static}]},self.headers)

    def send(self, observation):
        if observation.taz!=self.taz and self.mode=='federated':
            raise ValueError('A federated sender cannot write another TAZ')
        start=time.perf_counter_ns()
        try:
            self.transport('POST',self.urls['south']+'/iot/json?'+urlencode({'i':observation.device_id,'k':self.key}),
                dict(trafficFlow=observation.traffic_flow,observedAt=observation.observed_at,
                     sampleId=observation.sample_id,deliveryId=uuid.uuid4().hex,timeSlot=observation.time_slot,
                     flowIncrement=observation.flow_increment if observation.flow_increment is not None else observation.traffic_flow,
                     hourSampleId=observation.hour_sample_id or observation.sample_id,
                     hourStart=observation.hour_start or observation.observed_at,hourComplete=observation.hour_complete))
        except FiwareHTTPError as error:
            # IoT Agent can hide the broker's detailed validation error behind
            # ENTITY_GENERIC_ERROR. Retain its response and identify the sample.
            raise FiwareHTTPError(error.status,
                f'{error.message}; measurement context: TAZ={observation.taz}, '
                f'tenant={self.tenant}, device={observation.device_id}, '
                f'observedAt={observation.observed_at}'
            ) from error
        return (time.perf_counter_ns()-start)/1e6

    def entities(self, limit=100):
        entities=self.transport('GET',self.urls['broker']+'/v2/entities?'+urlencode({
            'type':'TrafficFlowObserved','options':'keyValues','limit':limit}),headers=self.headers)
        for entity in entities:
            road_name=decode_road_name(entity)
            if road_name is not None:
                entity['roadName']=road_name
                entity.pop('roadNameHex',None)
        return entities
