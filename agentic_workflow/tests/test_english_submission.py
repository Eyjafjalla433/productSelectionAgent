import json
from pathlib import Path
import unittest

from agentic_workflow.showcase import run_case, render
from agentic_workflow import Agent
from mvp.localization import message_locale
from mvp.server import AgentRuntime


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

    def test_chinese_followup_still_gets_an_english_reply(self):
        catalog = Path(__file__).resolve().parents[1] / 'mvp' / 'demo_data' / 'catalog.jsonl'
        runtime = AgentRuntime(Agent(catalog_path=catalog, trace_enabled=True))
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        self.assertTrue(first['products'])
        reply = runtime.chat(sid, '第一款多少钱？')
        self.assertEqual(runtime.sessions[sid].locale, 'en')
        self.assertNotRegex(reply['assistant']['message'], r'[\u3400-\u9fff]')
        self.assertIn('#1', reply['assistant']['message'])

    def test_live_interface_has_no_chinese_copy(self):
        root = Path(__file__).resolve().parents[1] / 'mvp' / 'static'
        for name in ('index.html', 'app.js'):
            self.assertNotRegex((root / name).read_text(encoding='utf-8'), r'[\u3400-\u9fff]')
