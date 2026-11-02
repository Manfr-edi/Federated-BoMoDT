"""Normalize Bologna detector CSVs to explicitly timestamped sensor observations."""
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
from zoneinfo import ZoneInfo
from .deployment import TAZS


def slot(hour):
    if not 0 <= hour <= 23: raise ValueError('Hour must be in 0..23')
    return f'{hour:02d}:00-{hour+1:02d}:00'


def utc_stamp(value):
    if isinstance(value,str): value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None: value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def time_window(day, hour):
    local=datetime.fromisoformat(day).replace(hour=hour,tzinfo=ZoneInfo('Europe/Rome'))
    # A repeated or nonexistent DST hour cannot be reconstructed from an hourly column.
    if local.replace(fold=0).utcoffset()!=local.replace(fold=1).utcoffset():
        raise ValueError('Ambiguous/nonexistent DST hour in source data; choose another hour')
    return utc_stamp(local),utc_stamp(local.astimezone(timezone.utc)+timedelta(hours=1))


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class Observation:
    source_id: str
    device_id: str
    entity_id: str
    taz: str
    edge_id: str
    road_name: str
    latitude: float
    longitude: float
    observed_at: str
    time_slot: str
    traffic_flow: float
    sample_id: str
    flow_increment: int | None = None
    hour_sample_id: str | None = None
    hour_start: str | None = None
    hour_complete: bool = True

    def to_dict(self): return asdict(self)


def read_observations(path, day, hours):
    hours=sorted(set(hours))
    if not hours: raise ValueError('Select at least one observation hour')
    result={}
    with Path(path).open(newline='',encoding='utf-8-sig') as handle:
        rows=csv.DictReader(handle,delimiter=';')
        required={'data','edge_id','codZone','ID_univoco_stazione_spira',*[slot(h) for h in hours]}
        if not required.issubset(rows.fieldnames or []):
            raise ValueError('CSV is missing required Bologna detector columns')
        for row in rows:
            if row['data']!=day: continue
            taz=row['codZone'].strip()
            if taz not in TAZS: raise ValueError(f'Unmapped TAZ: {taz}')
            edge=row['edge_id'].strip()
            if not edge or edge.lower()=='nan': raise ValueError('Detector is missing its SUMO edge mapping')
            source=row['ID_univoco_stazione_spira'].strip()
            identity=source+'|'+row.get('codice_spira','')+'|'+edge
            device='loop_'+hashlib.sha256(identity.encode()).hexdigest()[:20]
            for hour in hours:
                count=float(row[slot(hour)])
                if not math.isfinite(count) or count < 0 or count != int(count):
                    raise ValueError('Hourly traffic counts must be finite, nonnegative integers')
                stamp,_=time_window(day,hour)
                sample=hashlib.sha256((device+'|'+stamp).encode()).hexdigest()
                observation=Observation(source,device,'urn:ngsi-ld:TrafficFlowObserved:'+device,
                    taz,edge,row.get('Nome via',''),float(row.get('latitudine') or 0),
                    float(row.get('longitudine') or 0),stamp,slot(hour),count,sample)
                previous=result.get(sample)
                if previous and previous!=observation:
                    raise ValueError('Conflicting measurements for the same sensor and hour')
                result[sample]=observation
    if not result: raise ValueError('No observations for the selected date/hour')
    for hour in hours:
        if not any(o.time_slot==slot(hour) for o in result.values()):
            raise ValueError('Selected hour contains no observations')
    return sorted(result.values(),key=lambda o:(o.observed_at,o.taz,o.device_id))
