import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter


class ChinesePreferenceTests(unittest.TestCase):
    def test_unconfirmed_style_preference_is_explained_in_english(self):
        from mvp.explanations import explain_product
        from mvp.shopping_guide import preference_tradeoff
        product = {'match': explain_product({'title': 'Cotton tshirt'}, {'soft': {'style': ['loose fit']}})}
        text = preference_tradeoff([product])
        self.assertIn('loose fit', text)
        self.assertNotIn('宽松', text)

    def test_free_text_collects_multiple_preferences_without_a_pending_question(self):
        parsed = TurnIntentRouter().understand_turn('想要宽松一点，平时上班穿，最好透气舒服')
        values = {u.slot: u.values for u in parsed.slot_updates if u.operation == 'set'}
        self.assertEqual(values['style'], ('loose fit',))
        self.assertIn('work', values['use_case'])
        self.assertEqual(set(values['feature']), {'breathable', 'comfortable'})

    def test_negative_style_is_not_stored_as_positive(self):
        for text in ('不要太紧身，但要宽松一点', 'not slim fit, prefer loose fit'):
            updates = TurnIntentRouter().understand_turn(text).slot_updates
            self.assertTrue(any(u.slot == 'style' and u.operation == 'exclude' and 'slim fit' in u.values for u in updates))
            self.assertTrue(any(u.slot == 'style' and u.operation == 'set' and 'loose fit' in u.values for u in updates))
            self.assertFalse(any(u.slot == 'style' and u.operation == 'set' and 'slim fit' in u.values for u in updates))

    def test_multi_preference_revision_undo_redo(self):
        agent = Agent(search_function=lambda query, top_k: [], details_function=lambda ids: [], trace_enabled=True)
        agent.reset('zh', {})
        agent.respond('zh', '我想要T恤，宽松一点，上班穿，最好透气', 1)
        first = agent.memory.snapshot('zh')
        self.assertEqual(first.soft_preferences['style'][0].value, 'loose fit')
        self.assertEqual(first.soft_preferences['use_case'][0].value, 'work')
        agent.respond('zh', '改成修身，准备旅行穿', 2)
        second = agent.memory.snapshot('zh')
        self.assertEqual(second.soft_preferences['style'][0].value, 'slim fit')
        self.assertEqual(second.soft_preferences['use_case'][0].value, 'travel')
        agent.respond('zh', '撤销', 3)
        self.assertEqual(agent.memory.snapshot('zh').soft_preferences['style'][0].value, 'loose fit')
        agent.respond('zh', '重做', 4)
        self.assertEqual(agent.memory.snapshot('zh').soft_preferences['style'][0].value, 'slim fit')
        self.assertFalse(agent.errors)
