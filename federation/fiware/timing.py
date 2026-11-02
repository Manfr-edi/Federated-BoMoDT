"""Reproducible synthetic sub-hour arrivals constrained by real hourly counts."""
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import math
import random
from .observations import read_observations, utc_stamp


def source_period(start_time, duration_hours):
    start=datetime.fromisoformat(str(start_time))
    if start.tzinfo is not None or start.minute or start.second or start.microsecond:
        raise ValueError('Source CSV is hourly: choose an hour-aligned local start without a timezone suffix')
    if not isinstance(duration_hours,int) or not 1<=duration_hours<=168:
        raise ValueError('Duration must be between 1 and 168 source hours')
    return [start+timedelta(hours=i) for i in range(duration_hours)]


def read_period(path, start_time, duration_hours):
    periods=source_period(start_time,duration_hours)
    days={}
    for moment in periods: days.setdefault(moment.date().isoformat(),[]).append(moment.hour)
    observations=[]
    for day,hours in days.items(): observations.extend(read_observations(path,day,hours))
    return sorted(observations,key=lambda o:(o.observed_at,o.taz,o.device_id))


def minute_counts(observation, interval_seconds=60, seed=42):
    if interval_seconds not in (60,120,300,600):
        raise ValueError('Reporting interval must be 60, 120, 300 or 600 source seconds')
    count=int(observation.traffic_flow)
    key=f'{seed}|{observation.device_id}|{observation.observed_at}'
    rng=random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big'))
    buckets=3600//interval_seconds
    phase=rng.uniform(0,2*math.pi)
    weights=[math.exp(.65*math.sin(2*math.pi*i/buckets+phase)+rng.uniform(-.45,.45)) for i in range(buckets)]
    result=[0]*buckets
    for index in rng.choices(range(buckets),weights=weights,k=count): result[index]+=1
    return result


def timed_events(observation, interval_seconds=60, seed=42):
    counts=minute_counts(observation,interval_seconds,seed)
    start=datetime.fromisoformat(observation.observed_at.replace('Z','+00:00'))
    cumulative=0
    for i,increment in enumerate(counts):
        cumulative+=increment
        # Keep the last report inside [hour_start, hour_end), as required by SQL.
        offset=(i+1)*interval_seconds-.001
        sample=hashlib.sha256(f'{observation.sample_id}|bucket|{i}'.encode()).hexdigest()
        event=replace(observation,observed_at=utc_stamp(start+timedelta(seconds=offset)),
            sample_id=sample,traffic_flow=cumulative,flow_increment=increment,
            hour_sample_id=observation.sample_id,hour_start=observation.observed_at,
            hour_complete=i==len(counts)-1)
        yield offset,event
