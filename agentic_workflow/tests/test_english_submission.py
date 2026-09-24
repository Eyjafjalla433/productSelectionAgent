import json
from pathlib import Path
import unittest

from agentic_workflow.showcase import run_case, render
from mvp.localization import message_locale


class EnglishSubmissionTests(unittest.TestCase):
    def test_demo_and_download_are_english(self):
        report = run_case('demo')
        self.assertTrue(report['audit_valid'])
        self.assertEqual(report['handoff']['status'], 'finalized')
        self.assertNotRegex(json.dumps(report, ensure_ascii=False), r'[\u3400-\u9fff]')
        page = render(report)
        self.assertIn('<html lang="en">', page)
        self.assertNotRegex(page, r'[\u3400-\u9fff]')

    def test_input_language_does_not_change_output_locale(self):
        self.assertEqual(message_locale('我想买黑色T恤', 'zh'), 'en')
        self.assertEqual(message_locale('Undo', 'zh'), 'en')

    def test_live_interface_has_no_chinese_copy(self):
        root = Path(__file__).resolve().parents[1] / 'mvp' / 'static'
        for name in ('index.html', 'app.js'):
            self.assertNotRegex((root / name).read_text(encoding='utf-8'), r'[\u3400-\u9fff]')
