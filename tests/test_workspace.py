"""Cleanup boundaries and truthful container-status indicators."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from federation.fiware import workspace, jobs


class WorkspaceTests(unittest.TestCase):
    def test_cleanup_button_requires_selection_and_deletes_temporary_scenario(self):
        from streamlit.testing.v1 import AppTest
        from federation.fiware import ui
        from federation.models import ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            scenario=root/'scenarios/saved';scenario.mkdir(parents=True)
            (scenario/'scenario.json').write_text('{"status":"failed"}')
            with patch.object(ui,'ROOT',root),patch.object(jobs,'JOBS',root/'jobs'),patch.object(workspace,'list_jobs',return_value=[]):
                app=AppTest.from_file(str(ROOT/'dashboards/monolithic.py'),default_timeout=20).run()
                button=next(b for b in app.button if b.label=='Delete all saved scenarios')
                self.assertTrue(button.disabled)
                next(c for c in app.checkbox if c.label=='I want to permanently delete all saved scenarios').check().run()
                next(b for b in app.button if b.label=='Delete all saved scenarios').click().run()
                self.assertFalse(app.exception)
                self.assertFalse(scenario.exists())

    def test_cleanup_keeps_results_and_never_follows_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            scenario=root/'scenarios'/'saved';scenario.mkdir(parents=True)
            (scenario/'scenario.json').write_text('{}')
            for name in ['sessions','experiments','models','outside','scenarios/unmanaged']:
                (root/name).mkdir(parents=True)
                (root/name/'keep').write_text('keep')
            (root/'outside/scenario.json').write_text('{}')
            (root/'scenarios/linked').symlink_to(root/'outside',target_is_directory=True)
            with patch.object(jobs,'JOBS',root/'jobs'),patch.object(workspace,'list_jobs',return_value=[]):
                self.assertEqual(workspace.wipe_scenarios(root),1)
            self.assertFalse(scenario.exists())
            for name in ['sessions','experiments','models','outside','scenarios/unmanaged']:
                self.assertTrue((root/name/'keep').exists())
            self.assertTrue((root/'scenarios/linked').is_symlink())

    def test_cleanup_refuses_while_either_dashboard_is_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'scenarios/saved';p.mkdir(parents=True)
            (p/'scenario.json').write_text('{}')
            with patch.object(jobs,'JOBS',root/'jobs'),patch.object(workspace,'list_jobs',side_effect=lambda mode:[{'status':'running'}] if mode=='federated' else []):
                with self.assertRaisesRegex(RuntimeError,'both dashboards'): workspace.wipe_scenarios(root)
            self.assertTrue(p.exists())

    def test_indicator_distinguishes_stopped_partial_unhealthy_and_unknown(self):
        for count,status,state in [(0,'','stopped'),(6,'Up','partial'),(7,'Up (healthy)','ready'),(7,'Up (unhealthy)','partial'),(7,'Up (health: starting)','partial')]:
            raw='\n'.join(json.dumps({'State':'running','Status':status}) for _ in range(count))
            with patch.object(workspace.subprocess,'run',return_value=SimpleNamespace(stdout=raw)):
                result=workspace.service_status('monolithic')
            self.assertEqual(result['state'],state)
            self.assertEqual(result['running'],count)
        with patch.object(workspace.subprocess,'run',side_effect=OSError('docker unavailable')):
            self.assertEqual(workspace.service_status('federated')['state'],'unknown')
