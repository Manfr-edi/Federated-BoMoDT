"""Read QuantumLeap's Timescale persistence, matching BoMoDT's data path.

The historical SQL path is intentional: QL's Timescale query API is not supported
in all versions. No CSV or local shadow is used as a substitute for persisted data.
"""
from datetime import datetime
import os
from .deployment import endpoints, validate_tenant
from .observations import utc_stamp, time_window
from .text_codec import decode_road_name


class IncompleteHistoryError(ValueError):
    """Expose only missing identities so the emulator can retry through IoT Agent."""
    def __init__(self, missing_ids, expected_count):
        self.missing_ids=frozenset(missing_ids)
        super().__init__(f'FIWARE history incomplete: {len(self.missing_ids)} of {expected_count} samples missing')


class TimescaleHistory:
    def __init__(self, mode, taz, tenant):
        self.urls=endpoints(mode,taz)
        self.tenant=validate_tenant(tenant)
        self.taz=taz

    def query(self, start, end):
        import psycopg
        from psycopg import sql
        schema='mt'+self.tenant
        with psycopg.connect(host='127.0.0.1',port=self.urls['postgres_port'],
                dbname='quantumleap',user='postgres',password=os.environ.get('BOMODT_DB_PASSWORD','research'),
                connect_timeout=5) as db:
            table=db.execute("SELECT to_regclass(%s)",(schema+'.ettrafficflowobserved',)).fetchone()[0]
            if table is None: return []
            rows=db.execute(sql.SQL('SELECT to_jsonb(t) FROM {}.{} t WHERE observedat::timestamptz >= %s AND observedat::timestamptz < %s ORDER BY observedat, entity_id').format(
                sql.Identifier(schema),sql.Identifier('ettrafficflowobserved')),
                (datetime.fromisoformat(start.replace('Z','+00:00')),datetime.fromisoformat(end.replace('Z','+00:00')))).fetchall()
        result=[]
        for (row,) in rows:
            # Provisioning can create an entity before its first complete measurement.
            if not row.get('sampleid') or row.get('hourcomplete') is False: continue
            result.append(dict(sample_id=row.get('hoursampleid') or row['sampleid'],entity_id=row['entity_id'],
                traffic_flow=float(row['trafficflow']),observed_at=utc_stamp(row.get('hourstart') or row['observedat']),
                edge_id=row['edgeid'],taz=row['taz'],source_id=row['sourceid'],
                road_name=decode_road_name(row)))
        return result


def read_session(manifest, day, hour, history_factory=TimescaleHistory):
    start,end=time_window(day,hour)
    expected={o['sample_id']:o for o in manifest['observations'] if o['observed_at']==start}
    if not expected: raise ValueError('This replay has no measurements for that scenario hour')
    actual={}
    # Query each independent stack once; a monolith contains all zones in one schema.
    tazs=sorted({o['taz'] for o in expected.values()})
    for taz in (tazs[:1] if manifest['mode']=='monolithic' else tazs):
        for row in history_factory(manifest['mode'],taz,manifest['tenant']).query(start,end):
            sample=row['sample_id']
            if sample not in expected:
                raise ValueError('Unexpected sample in this replay tenant')
            source=expected[sample]
            if any(row[key]!=source[key] for key in ['entity_id','edge_id','taz','observed_at']):
                raise ValueError('Persisted measurement identity differs from its source manifest')
            if row['traffic_flow'] != source['traffic_flow']:
                raise ValueError('Persisted traffic count differs from the emulated measurement')
            if manifest.get('transport_schema_version',1)>=2 and row.get('road_name')!=source['road_name']:
                raise ValueError('Persisted road name differs from the original source label')
            if sample in actual and row!=actual[sample]:
                raise ValueError('Conflicting duplicate sample in FIWARE history')
            actual[sample]=row
    missing=set(expected)-set(actual)
    if missing: raise IncompleteHistoryError(missing,len(expected))
    return [actual[key] for key in sorted(actual)]
