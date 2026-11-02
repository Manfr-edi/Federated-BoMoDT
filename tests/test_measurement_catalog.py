"""Only real operations, with dashboard simulations as the default comparison."""
import json
from pathlib import Path
import tempfile
import unittest
from federation.fiware.measurements import measurement_catalog, select_measurements


class CatalogueTests(unittest.TestCase):
    def test_dashboard_defaults_to_simulations_and_can_reveal_other_sources(self):
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch
        from federation.fiware import ui
        from federation.models import ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            for name,dashboard in [('my-run',True),('validation',False)]:
                folder=root/'experiments'/name;folder.mkdir(parents=True)
                (folder/'summary.json').write_text(json.dumps(dict(status='completed',config=dict(mode='monolithic',control='local',routes='routes'))))
                (folder/'scenario-run.json').write_text(json.dumps(dict(mode='monolithic',integrated=True,scenario='shared',control='local')))
                (folder/'metrics').mkdir()
                (folder/'metrics/summary.json').write_text(json.dumps(dict(mode='monolithic',kind='simulation',status='completed',control='local',latency_ms={},dashboard_included=dashboard)))
                (folder/'metrics/resources.csv').write_text('mode,kind\n')
            replay=root/'sessions/monolithic/replay';replay.mkdir(parents=True)
            (replay/'manifest.json').write_text(json.dumps(dict(mode='monolithic',tenant='gw_test',status='verified',day='2025-01-01',hours=[8],observations=[])))
            (replay/'metrics').mkdir()
            (replay/'metrics/summary.json').write_text(json.dumps(dict(mode='monolithic',kind='emulation',status='verified',latency_ms={},dashboard_included=True)))
            (replay/'metrics/resources.csv').write_text('mode,kind\n')
            (root/'configs').symlink_to(ROOT/'configs',target_is_directory=True)
            with patch.object(ui,'ROOT',root):
                app=AppTest.from_file(str(ROOT/'dashboards/monolithic.py'),default_timeout=20).run()
                self.assertFalse(app.exception)
                select=next(s for s in app.multiselect if s.label=='Simulations to compare')
                self.assertEqual(len(select.options),1)
                self.assertIn('my-run',select.options[0])
                next(c for c in app.checkbox if c.label=='Include CLI and validation runs').check().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(next(s for s in app.multiselect if s.label=='Simulations to compare').options),2)
                next(r for r in app.radio if r.label=='Measurements to evaluate').set_value('Data emulation').run()
                self.assertFalse(app.exception)
                self.assertEqual(len(next(s for s in app.multiselect if s.label=='Emulations to compare').options),1)

    def test_no_report_duplicates_and_explicit_nondefault_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,origin,integrated,status in [('dashboard','dashboard',True,'completed'),
                ('validation','cli',True,'completed'),('offline','dashboard',False,'completed'),
                ('failed','dashboard',True,'failed')]:
                folder=root/'experiments'/name;folder.mkdir(parents=True)
                (folder/'summary.json').write_text(json.dumps(dict(status=status,config=dict(mode='monolithic',control='local',routes='scenario/routes.rou.xml'))))
                (folder/'scenario-run.json').write_text(json.dumps(dict(mode='monolithic',integrated=integrated,scenario='scenario',control='local')))
                (folder/'metrics').mkdir()
                (folder/'metrics/summary.json').write_text(json.dumps(dict(mode='monolithic',kind='simulation',status=status,dashboard_included=origin=='dashboard')))
            session=root/'sessions/monolithic/replay';session.mkdir(parents=True)
            (session/'manifest.json').write_text(json.dumps(dict(mode='monolithic',status='verified',tenant='gw_test')))
            all_runs=measurement_catalog(root,'simulation')
            self.assertEqual(len(all_runs),4)
            self.assertEqual([r['folder'].name for r in select_measurements(all_runs,['monolithic'])],['dashboard'])
            self.assertEqual(len(select_measurements(all_runs,['monolithic'],True,True,True)),4)
            self.assertEqual(len(measurement_catalog(root,'emulation')),1)

    def test_dashboard_job_identifies_runs_even_before_report_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'experiments/run';folder.mkdir(parents=True)
            (folder/'summary.json').write_text(json.dumps(dict(status='completed',config=dict(mode='federated',control='coordinated',routes='routes'))))
            (folder/'scenario-run.json').write_text(json.dumps(dict(mode='federated',integrated=True,scenario='shared')))
            job=root/'state/jobs/federated/job';job.mkdir(parents=True)
            (job/'job.json').write_text(json.dumps(dict(kind='simulate',status='completed',created_at=100,
                arguments=['simulate','--output','experiments/run'])))
            selected=select_measurements(measurement_catalog(root,'simulation'),['federated'])
            self.assertEqual(len(selected),1)
            self.assertEqual(selected[0]['origin'],'dashboard')
            self.assertEqual(selected[0]['created'],100)
