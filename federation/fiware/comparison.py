"""Presentation exports for one matched monolithic/federated pair."""
import csv
import hashlib
import json
from pathlib import Path
import zipfile
from .reports import export_report,read_json,write_csv
from .latency_metrics import PATHS

CHECKS=('kind','control','profile','integrated','control_protocol_version','grafana_probe',
        'step_seconds','seed','source_start','gui','gui_delay','replicas','routes_sha256','network_sha256','dashboard_included','completed_cycles','simulated_end_seconds')


def compatibility(reports):
    problems=[]
    if len(reports)!=2 or {r.get('mode') for r in reports}!={'monolithic','federated'}:
        return ['Select exactly one monolithic and one federated simulation.']
    for key in CHECKS:
        if reports[0].get(key)!=reports[1].get(key): problems.append('Different '+key.replace('_',' '))
    for r in reports:
        if r.get('status')!='completed': problems.append(r.get('mode','Run')+' is not completed')
        if r.get('kind')!='simulation': problems.append('Select simulations for this comparison')
        for key in ('control','routes_sha256','network_sha256'):
            if r.get(key) is None: problems.append('Missing '+key.replace('_',' '))
    return sorted(set(problems))


def export_comparison(folders,destination):
    reports=[];by_mode={}
    for folder in folders:
        out=export_report(folder);reports.append(read_json(out/'summary.json'))
        by_mode[reports[-1]['mode']]=Path(folder)
    problems=compatibility(reports)
    if problems: raise ValueError('; '.join(problems))
    reports.sort(key=lambda r:r['mode']!='monolithic')
    flavor='Active FIWARE control' if reports[0].get('integrated') else 'Offline baseline'
    out=Path(destination);out.mkdir(parents=True,exist_ok=True)
    for name in ('comparison-footprint','comparison-latency','comparison-policy-work'):
        for suffix in ('png','svg'):(out/(name+'.'+suffix)).unlink(missing_ok=True)
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    colors=['#2878b5','#e58b2a'];names=['Monolithic','Federated']
    figures=[];rows=[];coverage=[]
    def panel(ax,title,values,unit):
        bars=ax.bar(names,values,color=colors)
        ax.bar_label(bars,labels=[f'{v:.2f}' for v in values],padding=4)
        ax.set_ylim(0,max(values)*1.2 if max(values)>0 else 1)
        ax.set_title(title);ax.set_ylabel(unit);ax.grid(axis='y',alpha=.2)
    specifications=[('application_cpu','Application + SUMO CPU','CPU seconds',lambda r:r['resource_totals']['application'].get('sampled_cpu_seconds')),
        ('fiware_cpu','FIWARE CPU','Estimated CPU seconds',lambda r:r['resource_totals']['fiware'].get('sampled_cpu_seconds')),
        ('operation_time','Complete run duration','Wall-clock seconds',lambda r:r.get('operation_wall_seconds')),
        ('fiware_memory','FIWARE memory peak','MiB (Docker usage)',lambda r:r['resource_peaks']['fiware'].get('memory_mib'))]
    use_uss=all(r['resource_peaks']['application'].get('uss_mib') is not None for r in reports)
    specifications.insert(2,('application_memory','Application + SUMO memory peak',
        'MiB ('+('USS' if use_uss else 'summed RSS')+')',lambda r:r['resource_peaks']['application'].get('uss_mib' if use_uss else 'rss_mib')))
    available=[]
    for key,title,unit,getter in specifications:
        values=[getter(r) for r in reports]
        if all(v is not None for v in values):
            available.append((key,title,unit,values))
            rows.append(dict(measurement=title,unit=unit,monolithic=values[0],federated=values[1],
                federated_minus_monolithic=values[1]-values[0],
                federated_over_monolithic=values[1]/values[0] if values[0] else None))
        else: coverage.append(dict(measurement=title,reason='Not recorded in both runs; omitted from plots'))
    if available:
        fig,axes=plt.subplots((len(available)+1)//2,2,figsize=(10,3.5*((len(available)+1)//2)),squeeze=False)
        for ax,(_,title,unit,values) in zip(axes.flat,available): panel(ax,title,values,unit)
        for ax in list(axes.flat)[len(available):]:
            ax.axis('off');ax.text(.1,.7,'Reading this report\n\nSame scenario and controller\nCPU seconds = total measured work\nMemory scopes are separate\nMissing metrics: see coverage.csv\nOne pair is descriptive, not a ranking',va='top')
        fig.suptitle(flavor+' · matched whole-operation cost');fig.tight_layout()
        figures.append(('comparison-footprint',fig))
    latency_names=[(PATHS[0],'Snapshot → Grafana query response'),
        (PATHS[1],'Persistence confirmation → SUMO action'),('control_closed_loop_ms','Complete control loop (without physics)')]
    available=[]
    for metric,title in latency_names:
        values=[r['latency_ms'].get(metric) for r in reports]
        if not all(values):
            coverage.append(dict(measurement=title,reason='No agents, historical instrumentation or missing responses; omitted'))
            continue
        available.append((title,values))
        for statistic in ('p50','p95'):
            rows.append(dict(measurement=title+' · '+statistic,unit='ms',monolithic=values[0][statistic],federated=values[1][statistic],
                federated_minus_monolithic=values[1][statistic]-values[0][statistic],federated_over_monolithic=values[1][statistic]/values[0][statistic] if values[0][statistic] else None))
    if available:
        fig,axes=plt.subplots(len(available),1,figsize=(10,3*len(available)),squeeze=False)
        for ax,(title,values) in zip(axes.flat,available):
            numbers=[v[k] for v in values for k in ('p50','p95')]
            bars=ax.barh(['Monolithic median','Monolithic p95','Federated median','Federated p95'],numbers,color=[colors[0]]*2+[colors[1]]*2)
            ax.bar_label(bars,labels=[f'{v:.2f} ms' for v in numbers],padding=4)
            ax.set_xlim(0,max(numbers)*1.25 if max(numbers)>0 else 1)
            ax.set_title(title);ax.set_xlabel('Real milliseconds');ax.grid(axis='x',alpha=.2)
        fig.suptitle(flavor+' · observed latency · same scenario/controller');fig.tight_layout()
        figures.append(('comparison-latency',fig))
    policy=[]
    for report in reports:
        with (by_mode[report['mode']]/'metrics/policy-work.csv').open() as handle:
            policy.append({r['taz']:r for r in csv.DictReader(handle) if r.get('taz')})
    common=sorted(set(policy[0])&set(policy[1]))
    cpu_available=common and all(policy[i][t].get('mean_inference_cpu_ms') for i in (0,1) for t in common)
    if cpu_available:
        fig,ax=plt.subplots(figsize=(10,4))
        for i,name in enumerate(names):
            ax.bar([j+(i-.5)*.36 for j in range(len(common))],
                [float(policy[i][t]['mean_inference_cpu_ms']) for t in common],width=.36,label=name,color=colors[i])
        ax.set_xticks(range(len(common)),common);ax.set_xlabel('Logical TAZ controller');ax.set_ylabel('CPU ms per decision')
        ax.set_title(flavor+' · CPU work per local decision');ax.legend();fig.tight_layout()
        figures.append(('comparison-policy-work',fig))
    else: coverage.append(dict(measurement='Per-controller CPU',reason='Not recorded in both runs or no agent controller; omitted'))
    with PdfPages(out/'comparison.pdf') as pdf:
        for name,fig in figures:
            for suffix in ('png','svg'):fig.savefig(out/(name+'.'+suffix),dpi=180)
            pdf.savefig(fig);plt.close(fig)
    write_csv(out/'comparison.csv',rows);write_csv(out/'coverage.csv',coverage)
    (out/'runs.json').write_text(json.dumps(reports,indent=2))
    text=['Matched run comparison','======================','']
    text += [r['mode']+': '+r['run'] for r in reports]
    text += ['','Same scenario hashes/controller/integration settings validated.',
        'CPU seconds measure work; CPU cores measure parallel utilization. No 0-100% machine normalization is guessed.',
        'Grafana ends at a datasource response, not browser rendering.',
        'Persistence starts at the first historical readback, not the exact DB commit.',
        'Initial observations may include startup delays. Timing intervals overlap and must not be added.',
        'Missing metrics are listed in coverage.csv and omitted from presentation plots.',
        'One matched pair is descriptive evidence, not a statistical architecture ranking.']
    (out/'READ-ME.txt').write_text('\n'.join(text))
    with zipfile.ZipFile(out/'comparison.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in out.iterdir():
            if p.name!='comparison.zip':z.write(p,p.name)
    return out
