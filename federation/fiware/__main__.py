"""CLI shared by the two Streamlit control rooms and reproducible batch runs."""
import argparse
import subprocess
from .deployment import compose_command, generate
from .emulator import replay
from .scenarios import build_scenario, run_scenario


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    stack=commands.add_parser('stack')
    stack.add_argument('action',choices=['generate','up','stop','ps','logs','stats'])
    stack.add_argument('--mode',choices=['monolithic','federated'],required=True)
    emulate=commands.add_parser('emulate')
    emulate.add_argument('--mode',choices=['monolithic','federated'],required=True)
    emulate.add_argument('--source',required=True)
    emulate.add_argument('--date')
    choice=emulate.add_mutually_exclusive_group(required=True)
    choice.add_argument('--start-time',help='Hour-aligned local ISO start, e.g. 2025-01-01T09:00')
    emulate.add_argument('--duration-hours',type=int,default=1)
    emulate.add_argument('--temporal',action='store_true')
    emulate.add_argument('--speed',type=float,default=60.)
    emulate.add_argument('--interval-seconds',type=int,default=60)
    emulate.add_argument('--seed',type=int,default=42)
    choice.add_argument('--hours',type=int,nargs='+')
    emulate.add_argument('--delay-ms',type=float,default=0)
    emulate.add_argument('--verify-timeout',type=float,default=120)
    prepare=commands.add_parser('prepare')
    prepare.add_argument('--manifest',required=True)
    prepare.add_argument('--date')
    prepare.add_argument('--hour',type=int,required=True)
    prepare.add_argument('--profile',required=True)
    prepare.add_argument('--output',required=True)
    prepare.add_argument('--noise',type=float,default=1.)
    prepare.add_argument('--seed',type=int,default=42)
    prepare.add_argument('--candidates')
    run=commands.add_parser('simulate')
    run.add_argument('--scenario',required=True)
    run.add_argument('--mode',choices=['monolithic','federated'],required=True)
    run.add_argument('--control',choices=['none','local','coordinated'],default='none')
    run.add_argument('--output',required=True)
    run.add_argument('--gui',action='store_true')
    run.add_argument('--gui-delay',type=float,default=20.)
    run.add_argument('--offline',action='store_true',help='Legacy SUMO/RL baseline without FIWARE control traffic')
    report=commands.add_parser('report')
    report.add_argument('--run',required=True)
    run.add_argument('--replicas',action='store_true')
    args=parser.parse_args()
    if args.command=='emulate' and args.hours is not None and not args.date:
        parser.error('--date is required with --hours')
    if args.command=='stack':
        if args.action=='generate': print(generate(args.mode))
        else: subprocess.run(compose_command(args.mode,args.action),check=True)
    elif args.command=='emulate':
        replay(args.mode,args.source,args.date,args.hours,args.delay_ms,args.verify_timeout,
            start_time=args.start_time,duration_hours=args.duration_hours,temporal=args.temporal,
            speed=args.speed,interval_seconds=args.interval_seconds,seed=args.seed)
    elif args.command=='prepare':
        build_scenario(args.manifest,args.hour,args.profile,args.output,args.noise,args.seed,args.candidates,day=args.date)
    elif args.command=='report':
        from .reports import export_report
        print(export_report(args.run))
    else: run_scenario(args.scenario,args.mode,args.control,args.output,args.replicas,args.gui,args.gui_delay,integrated=not args.offline)


if __name__=='__main__': main()
