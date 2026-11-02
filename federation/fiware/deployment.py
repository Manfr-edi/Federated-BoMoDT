"""Generate two independent, inspectable Compose deployments from one contract.

The federated deployment has one private network and one full service stack per
TAZ. The monolith has one stack for all TAZs. No fixed container names are used.
"""
import json
from pathlib import Path
import re
from ..models import ROOT, VENDOR

TAZS = ('F', 'B', 'H', 'T', 'O', 'P', 'Q', 'D', 'G', 'M', 'L')


def validate_mode(mode):
    if mode not in ('monolithic', 'federated'):
        raise ValueError('Unknown deployment mode')
    return mode


def validate_tenant(tenant):
    if not re.fullmatch(r'gw_[a-z0-9_]{1,40}', tenant):
        raise ValueError('Invalid replay tenant')
    return tenant


def endpoints(mode, taz):
    validate_mode(mode)
    if taz not in TAZS:
        raise ValueError('Unknown TAZ')
    base = 11000 if mode == 'monolithic' else 12000 + TAZS.index(taz)*10
    return dict(broker=f'http://127.0.0.1:{base}', north=f'http://127.0.0.1:{base+1}',
                south=f'http://127.0.0.1:{base+2}', quantumleap=f'http://127.0.0.1:{base+3}',
                grafana=f'http://127.0.0.1:{base+4}', postgres_port=base+5)


def service_key(mode, taz, service):
    return ('city' if mode == 'monolithic' else taz.lower()) + '-' + service


def grafana_dashboard():
    """Read the same QuantumLeap-owned table used by scenario reconstruction."""
    datasource = {'type':'postgres', 'uid':'traffic-history'}
    table = '${tenant:raw}.ettrafficflowobserved'
    condition = " WHERE $__timeFilter(observedat) AND taz IN (${taz:sqlstring})"
    def panel(pid, title, kind, query, x, y, w=12, h=9):
        return dict(id=pid,title=title,type=kind,gridPos=dict(x=x,y=y,w=w,h=h),
                    datasource=datasource,targets=[dict(refId='A',format='time_series' if kind=='timeseries' else 'table',rawQuery=True,rawSql=query)])
    return dict(uid='bologna-traffic',title='Bologna Traffic Observations',schemaVersion=36,version=2,
        refresh='10s',time={'from':'now-1h','to':'now'},
        templating={'list':[
            dict(name='tenant',label='Replay history',type='query',datasource=datasource,refresh=1,
                 query="SELECT nspname AS __text, nspname AS __value FROM pg_namespace WHERE nspname ~ '^mtgw_[a-z0-9_]+$' ORDER BY 1"),
            dict(name='taz',label='TAZ',type='query',datasource=datasource,refresh=1,multi=True,
                 includeAll=True,query=f'SELECT DISTINCT taz FROM {table} ORDER BY 1')
        ]},panels=[
            panel(1,'Observed hourly counts by sensor','timeseries',
                  f"SELECT DISTINCT observedat AS time, taz || ':' || sourceid AS metric, trafficflow::double precision AS value FROM {table}"+condition+' ORDER BY 1',0,0,24),
            panel(3,'Crossings per reporting interval','timeseries',
                  f"SELECT DISTINCT observedat AS time, taz || ':' || sourceid AS metric, (to_jsonb(t)->>'flowincrement')::double precision AS value FROM {table} t"+condition+' ORDER BY 1',0,18,24),
            panel(2,'Measurements used to reconstruct scenarios','table',
                  f"SELECT DISTINCT observedat, taz, sourceid, edgeid, convert_from(decode(roadnamehex, 'hex'), 'UTF8') AS road_name, trafficflow, timeslot FROM {table} t"+condition+
                  " AND COALESCE((to_jsonb(t)->>'hourcomplete')::boolean, true) ORDER BY observedat, taz, sourceid",0,9,24)
        ])


def control_dashboard():
    """Inspect the same persisted control events that gate simulation advance."""
    dashboard=grafana_dashboard()
    dashboard.update(uid='bologna-control',title='Bologna Simulation Control',version=1)
    table='${tenant:raw}.ettwincontrolevent'
    dashboard['templating']['list'][0]['query']="SELECT nspname AS __text, nspname AS __value FROM pg_namespace WHERE nspname ~ '^mtgw_sim_[a-z0-9_]+$' ORDER BY 1"
    dashboard['templating']['list'][1]['query']=f'SELECT DISTINCT taz FROM {table} ORDER BY 1'
    where=" WHERE $__timeFilter(observedat) AND taz IN (${taz:sqlstring}) AND eventid IS NOT NULL"
    panel=dashboard['panels'][0]
    panel.update(title='Persisted control events by stage',gridPos=dict(x=0,y=0,w=24,h=8))
    panel['targets'][0]['rawSql']=f'SELECT observedat AS time, eventkind AS metric, count(DISTINCT eventid)::double precision AS value FROM {table}'+where+' GROUP BY observedat, eventkind ORDER BY 1'
    state=dashboard['panels'][-1]
    state.update(title='Observations, coordination, commands and feedback',gridPos=dict(x=0,y=8,w=24,h=12))
    state['targets'][0]['rawSql']=f"SELECT DISTINCT observedat, taz, eventkind, sequence, simulationseconds, convert_from(decode(payloadhex, 'hex'), 'UTF8') AS payload FROM {table}"+where+' ORDER BY observedat, taz, eventkind'
    dashboard['panels']=[panel,state]
    return dashboard


def generate(mode, destination=None):
    validate_mode(mode)
    destination = Path(destination or ROOT/'deployments'/mode).resolve()
    destination.mkdir(parents=True,exist_ok=True)
    images = json.loads((ROOT/'configs/fiware-images.json').read_text())
    stacks = ['city'] if mode == 'monolithic' else [t.lower() for t in TAZS]
    services, networks, volumes = {}, {}, {}
    for stack in stacks:
        prefix = stack+'-'
        base = 11000 if mode=='monolithic' else 12000+TAZS.index(stack.upper())*10
        networks[stack] = {}
        def host(name): return prefix+name
        def svc(name, image, **kwargs):
            services[host(name)] = dict(image=images[image],networks=[stack],**kwargs)
        def bind(offset, port): return [f'127.0.0.1:{base+offset}:{port}']
        for db in ['mongo','timescale','grafana']:
            volumes[host(db)] = {}
        svc('mongo','mongo',volumes=[f'{host("mongo")}:/data/db'],
            healthcheck=dict(test=['CMD','mongo','--quiet','--eval','db.adminCommand({ping:1}).ok'],interval='5s',timeout='5s',retries=30))
        # Orion-LD 1.5.1's image ENTRYPOINT already supplies -fg, -multiservice
        # and -ngsiv1Autocast. Its parser rejects repeated scalar/boolean options.
        # Only append deployment-specific arguments; preserve the image entrypoint.
        # Explicit, identical capacity in both architectures. With persisted
        # replay tenants, the legacy driver in Orion-LD 1.5.1 can exhaust its
        # default pool while /version still responds. Database-backed readiness
        # in FiwareClient additionally catches starvation before ingestion.
        svc('orion','orion',ports=bind(0,1026),command=['-dbhost',host('mongo'),'-logLevel','WARN','-dbPoolSize','64'],
            depends_on={host('mongo'):{'condition':'service_healthy'}},
            healthcheck=dict(test=['CMD','curl','--fail','--silent','--show-error',
                'http://127.0.0.1:1026/version'],interval='5s',timeout='5s',start_period='15s',retries=30))
        svc('iot','iot',ports=bind(1,4041)+bind(2,7896),
            depends_on={host('mongo'):{'condition':'service_healthy'},host('orion'):{'condition':'service_healthy'}},
            environment=dict(IOTA_CB_HOST=host('orion'),IOTA_CB_PORT='1026',IOTA_NORTH_PORT='4041',
                IOTA_REGISTRY_TYPE='mongodb',IOTA_MONGO_HOST=host('mongo'),IOTA_MONGO_PORT='27017',
                IOTA_MONGO_DB='iotagentjson',IOTA_HTTP_PORT='7896',IOTA_PROVIDER_URL=f'http://{host("iot")}:4041',
                IOTA_CB_NGSI_VERSION='v2',IOTA_TIMESTAMP='false',IOTA_AUTOCAST='true',
                IOTA_DEFAULT_RESOURCE='/iot/json',IOTA_LOG_LEVEL='WARN'))
        svc('timescale','timescale',ports=bind(5,5432),
            environment=dict(POSTGRES_USER='postgres',POSTGRES_PASSWORD='research',POSTGRES_DB='quantumleap'),
            volumes=[f'{host("timescale")}:/var/lib/postgresql/data',
                f'{ROOT / "infrastructure/timescale/init.sql"}:/docker-entrypoint-initdb.d/010-quantumleap.sql:ro'],
            healthcheck=dict(test=['CMD-SHELL','pg_isready -U postgres -d quantumleap'],interval='5s',timeout='5s',retries=30))
        svc('redis','redis')
        svc('quantumleap','quantumleap',ports=bind(3,8668),
            depends_on={host('timescale'):{'condition':'service_healthy'},host('redis'):{'condition':'service_started'}},
            environment=dict(QL_DEFAULT_DB='timescale',POSTGRES_HOST=host('timescale'),POSTGRES_PORT='5432',
                POSTGRES_DB_NAME='quantumleap',POSTGRES_DB_USER='postgres',POSTGRES_DB_PASS='research',
                POSTGRES_USE_SSL='False',REDIS_HOST=host('redis'),CACHE_QUERIES='False',
                USE_GEOCODING='False',LOGLEVEL='WARNING'))
        provisioning = destination/stack/'grafana'
        (provisioning/'provisioning/datasources').mkdir(parents=True,exist_ok=True)
        (provisioning/'provisioning/dashboards').mkdir(parents=True,exist_ok=True)
        (provisioning/'dashboards').mkdir(exist_ok=True)
        (provisioning/'provisioning/datasources/traffic.yaml').write_text(
            'apiVersion: 1\ndatasources:\n  - name: Traffic History\n    uid: traffic-history\n'
            f'    type: postgres\n    access: proxy\n    url: {host("timescale")}:5432\n'
            '    database: quantumleap\n    user: postgres\n    secureJsonData:\n      password: research\n'
            '    jsonData:\n      sslmode: disable\n      postgresVersion: 1300\n      timescaledb: true\n    isDefault: true\n')
        (provisioning/'provisioning/dashboards/traffic.yaml').write_text(
            'apiVersion: 1\nproviders:\n  - name: Bologna\n    type: file\n    options:\n      path: /var/lib/grafana/dashboards\n')
        (provisioning/'dashboards/traffic.json').write_text(json.dumps(grafana_dashboard(),indent=2))
        (provisioning/'dashboards/control.json').write_text(json.dumps(control_dashboard(),indent=2))
        svc('grafana','grafana',ports=bind(4,3000),
            depends_on={host('timescale'):{'condition':'service_healthy'}},
            environment=dict(GF_SECURITY_ALLOW_EMBEDDING='true',GF_AUTH_ANONYMOUS_ENABLED='true',
                GF_AUTH_ANONYMOUS_ORG_ROLE='Viewer',GF_SECURITY_ADMIN_PASSWORD='research'),
            volumes=[f'{host("grafana")}:/var/lib/grafana',
                f'{provisioning / "provisioning"}:/etc/grafana/provisioning:ro',
                f'{provisioning / "dashboards"}:/var/lib/grafana/dashboards:ro'])
    compose=dict(name='bomodt-'+mode,services=services,networks=networks,volumes=volumes)
    path=destination/'compose.json'
    path.write_text(json.dumps(compose,indent=2)+'\n')
    return path


def compose_command(mode, action):
    if action not in ('up','stop','ps','logs','stats'):
        raise ValueError('Unsupported stack operation')
    path=ROOT/'deployments'/validate_mode(mode)/'compose.json'
    # Re-render on startup so an existing generated file cannot retain fixed bugs.
    # Read-only diagnostics keep the configuration of the current deployment.
    if action=='up' or not path.exists(): generate(mode)
    arguments={'up':['up','-d'],'stop':['stop'],'ps':['ps','--all','--format','json'],
               'logs':['logs','--tail','150'],'stats':['stats','--no-stream','--format','json']}
    return ['docker','compose','-p','bomodt-'+mode,'-f',str(path),*arguments[action]]
