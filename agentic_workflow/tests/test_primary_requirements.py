import unittest

from agentic_workflow import Agent
from intent_router.models import SlotUpdate
from shopping_agent.requirement_parser import PrimaryRequirementParser
from shopping_agent.tests.test_model_provider import FakeProvider
from mvp.server import AgentRuntime
from agentic_workflow.tests import test_material_reversal


def edit(slot, op, values, evidence, tier=None):
    return dict(slot=slot, operation=op, values=values, evidence=evidence, constraint_type=tier)


class PrimaryRequirementTests(unittest.TestCase):
    def test_model_controls_tier_instead_of_rule(self):
        parser = PrimaryRequirementParser(FakeProvider({'updates': [
            edit('color', 'set', ['blue'], 'blue', 'soft')]}))
        out = parser.enhance('blue', [SlotUpdate('color', 'set', ('blue',), 'hard')])
        self.assertTrue(out.replaces_rules)
        self.assertEqual(out.updates[0].constraint_type, 'soft')

    def test_invalid_batch_falls_back_atomically_and_retains_usage(self):
        parser = PrimaryRequirementParser(FakeProvider({'updates': [
            edit('color', 'set', ['blue'], 'blue', 'hard'),
            edit('material', 'set', ['invented'], 'blue', 'hard')]}))
        out = parser.enhance('blue', ())
        self.assertFalse(out.replaces_rules)
        self.assertEqual(out.updates, ())
        self.assertGreater(out.usage['prompt_tokens'], 0)
        self.assertFalse(PrimaryRequirementParser(FakeProvider(error=True)).enhance('blue', ()).replaces_rules)

    def test_empty_success_suppresses_false_rule_extraction(self):
        out = PrimaryRequirementParser(FakeProvider()).enhance('not a shopping request', ())
        self.assertTrue(out.replaces_rules)
        self.assertEqual(out.updates, ())

    def test_context_supports_reference_without_inventing_value(self):
        parser = PrimaryRequirementParser(FakeProvider({'updates': [
            edit('material', 'exclude', ['cotton'], "I don't want that material")]}))
        message = "I don't want that material"
        self.assertFalse(parser.enhance(message, ()).replaces_rules)
        self.assertTrue(parser.enhance(message, (), context={
            'current_state': {'hard': {'material': 'cotton'}}}).replaces_rules)

    def test_runtime_reversal_clear_and_undo(self):
        runtime = test_material_reversal.MaterialReversalTests.runtime()
        provider = FakeProvider()
        runtime.agent.requirement_enhancer = PrimaryRequirementParser(provider)
        sid = runtime.new_session()['session_id']
        provider.data = {'updates': [edit(k, 'set', [v], v, 'hard') for k, v in
                                      [('category', 'shirt'), ('color', 'blue'), ('material', 'cotton')]]}
        before = runtime.chat(sid, 'I want a cotton blue shirt')
        provider.data = {'updates': [edit('material', 'exclude', ['cotton'], "don't want cotton")]}
        after = runtime.chat(sid, "I don't want cotton anymore")
        self.assertEqual(after['receipt']['hard'], {'category': 'shirt', 'color': 'blue'})
        self.assertEqual(after['receipt']['excluded']['material'], ['cotton'])
        self.assertEqual({p['parent_asin'] for p in after['products']}, {'linen', 'silk'})
        self.assertTrue(after['receipt']['model_assist']['replaces_rules'])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], before['receipt']['hard'])
        provider.data = {'updates': [edit('material', 'clear', [], 'Any material is fine')]}
        cleared = runtime.chat(sid, 'Any material is fine')
        self.assertNotIn('material', cleared['receipt']['hard'])
        self.assertNotIn('material', cleared['receipt']['excluded'])

    def test_provider_factory_selects_full_cloud_parsers(self):
        from mvp.demo import DEMO_CATALOG
        from shopping_agent.requirement_enhancer import RequirementEnhancer
        for name in ('deepseek', 'aws_bedrock_gateway', 'local'):
            with self.subTest(provider=name):
                provider = FakeProvider()
                provider.name = name
                runtime = AgentRuntime.create(DEMO_CATALOG, provider=provider)
                self.assertIsInstance(runtime.agent.requirement_enhancer,
                                      RequirementEnhancer if name == 'local' else PrimaryRequirementParser)
                self.assertEqual(runtime.response_writer is not None, name != 'local')
