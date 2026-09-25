import json
from pathlib import Path
import unittest

from agentic_workflow.showcase import run_case, render, description_section
from agentic_workflow import Agent
from mvp.localization import message_locale
from mvp.server import AgentRuntime


class EnglishSubmissionTests(unittest.TestCase):
    def test_clarification_replay_keeps_deferred_update_through_detours(self):
        report = run_case('demo', 'clarification')
        results = [turn['result'] for turn in report['turns']]
        self.assertEqual(len(results), 10)
        for result in results[1:6]:
            self.assertNotIn('material', result['receipt']['soft'])
            self.assertEqual(result['receipt']['comparison_reference_question']['pending_requirements'], 'I prefer cotton')
        self.assertIn('#1:', results[4]['assistant']['message'])
        self.assertIn('#3:', results[5]['assistant']['message'])
        self.assertEqual(results[6]['receipt']['soft']['material'], ['cotton'])
        self.assertNotIn('material', results[7]['receipt']['soft'])
        self.assertEqual(results[7]['selection_state']['selected_asins'], results[6]['selection_state']['selected_asins'])
        self.assertEqual(results[8]['handoff']['comparison_scope'], 'saved_options')
        self.assertTrue(report['audit_valid'])
        self.assertEqual(report['handoff']['status'], 'finalized')
        page = render(report)
        self.assertEqual(page.count('<section class="step"'), 10)
        self.assertEqual(page.count('<div class="confirmed">'), 1)
        self.assertNotRegex(page, r'[\u3400-\u9fff]')

    def test_revisions_replay_answers_old_item_and_reverses_requirement_change(self):
        report = run_case('demo', 'revisions')
        results = [turn['result'] for turn in report['turns']]
        self.assertEqual([len(result['products']) for result in results], [1, 5, 1, 5, 5, 5])
        self.assertIn('previous list', results[1]['assistant']['message'])
        self.assertEqual(results[1]['receipt']['compound_request']['detail_context']['parent_asin'],
                         results[0]['products'][0]['parent_asin'])
        self.assertNotIn('feature', results[1]['receipt']['hard'])
        self.assertEqual(results[2]['receipt']['hard'], results[0]['receipt']['hard'])
        self.assertEqual(results[2]['products'], results[0]['products'])
        self.assertEqual(results[3]['receipt']['soft']['feature'], ['breathable'])
        self.assertTrue(report['audit_valid'])
        self.assertEqual(report['handoff']['status'], 'finalized')
        page = render(report)
        self.assertEqual(page.count('<section class="step"'), 6)
        self.assertEqual(page.count('<div class="confirmed">'), 1)
        self.assertNotRegex(page, r'[\u3400-\u9fff]')

    def test_flexible_replay_preserves_context_then_undoes_preference_before_finalizing(self):
        report = run_case('demo', 'flexible')
        self.assertEqual(len(report['turns']), 7)
        self.assertTrue(report['audit_valid'])
        results = [turn['result'] for turn in report['turns']]
        self.assertEqual(results[2]['receipt']['pre_reason'], 'product_detail')
        self.assertEqual(results[2]['products'], results[1]['products'])
        self.assertEqual(results[3]['receipt']['preference_comparison_answer']['rank'], 3)
        self.assertEqual(results[3]['receipt']['soft']['style'], ['loose fit'])
        self.assertNotIn("couldn't find any new", results[3]['assistant']['message'])
        self.assertNotIn('style', results[4]['receipt']['soft'])
        self.assertEqual(results[4]['products'], results[0]['products'])
        self.assertEqual(report['handoff']['status'], 'finalized')
        page = render(report)
        self.assertEqual(page.count('<section class="step"'), 7)
        self.assertEqual(page.count('<div class="confirmed">'), 1)
        self.assertIn('07 / 07', page)
        self.assertIn('Explore 7 executed conversation turns', page)
        self.assertNotIn('index===3', page)
        self.assertNotRegex(page, r'[\u3400-\u9fff]')

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

    def test_comparison_keeps_requested_unknowns_and_shared_preferences_visible(self):
        def values(value):
            return {asin: {'value': value, 'source_type': 'explicit'} for asin in ('A', 'B')}
        handoff = {'selected_products': [{'parent_asin': asin, 'title': asin} for asin in ('A', 'B')],
                   'requirements': {'hard': {'price_max': 30}, 'soft': {'material': ['cotton']}},
                   'comparison_assist': {'schema_version': 'description-comparison.v1', 'status': 'offline_preview',
                       'objective_comparison': {'comparison_matrix': [
                           {'dimension': 'brand', 'values': values('Example')},
                           {'dimension': 'material', 'values': values('Cotton')},
                           {'dimension': 'price', 'values': values(None)}], 'product_assessments': []},
                       'personalized_comparison': {'personalization_applied': False, 'products': []}}}
        before = json.dumps(handoff)
        page = description_section(handoff)
        self.assertLess(page.index('Price<small>Your requirement'), page.index('Material<small>Your preference'))
        self.assertLess(page.index('Material<small>Your preference'), page.index('Shared details'))
        self.assertIn('<td>Unknown</td>', page)
        self.assertEqual(json.dumps(handoff), before)

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
