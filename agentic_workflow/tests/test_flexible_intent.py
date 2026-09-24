import unittest
from dataclasses import replace

from agentic_workflow import Agent
from shopping_agent.policy import missing_detail_question
from shopping_agent.retrieval import category_matches


class FlexibleIntentTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def search(query, top_k):
            self.calls.append((query, top_k))
            return [{'product_id': 'TEE', 'score': 1.5}]
        def details(ids):
            return [{'product_id': 'TEE', 'found': True, 'title': 'Black cotton tshirt',
                     'color': 'black', 'bullet_point': 'Cotton regular fit tee'}]
        self.agent = Agent(search_function=search, details_function=details, trace_enabled=True)
        self.agent.reset('test', {})

    def test_exact_reported_sentence_extracts_everything_and_searches(self):
        result = self.agent.respond('test', 'I want to buy a black tshirt around $30', 1)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints, {'category': 't-shirt', 'color': 'black'})
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)
        self.assertIsNone(result['ask_attribute'])
        self.assertEqual(len(result['recommendations']), 1)
        self.assertTrue(self.calls)
        self.assertIn("doesn't include prices", result['message'])
        self.assertEqual(state.suggestions['clarification_count'], 0)

    def test_long_fragment_and_multisentence_extract_all_slots(self):
        for text in ('A black cotton tshirt in size M around $30 please',
                     "I'm looking for a tshirt. Black cotton, size M, around $30."):
            with self.subTest(text=text):
                parsed = self.agent.router.understand_turn(text)
                sets = {u.slot: u.values for u in parsed.slot_updates if u.operation == 'set'}
                for key, value in {'category': ('t-shirt',), 'color': ('black',),
                                   'material': ('cotton',), 'size': ('m',), 'budget_target': (30.0,)}.items():
                    self.assertEqual(sets[key], value)

    def test_pending_category_answer_can_supply_many_values(self):
        self.agent.respond('test', 'Hello', 1)
        result = self.agent.respond('test', 'black cotton tshirt around $30', 2)
        self.assertIsNone(result['ask_attribute'])
        self.agent.respond('test', 'regular fit for everyday', 3)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints['color'], 'black')
        self.assertEqual(state.hard_constraints['material'], 'cotton')
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)
        question = missing_detail_question(state, 'feature', 'negative_feedback')
        self.assertNotIn(question.question['target_slot'], {'category', 'color', 'material', 'style', 'use_case', 'budget'})

    def test_followup_selects_an_unknown_dimension(self):
        self.agent.respond('test', 'black cotton tshirt regular fit around $30', 1)
        state = self.agent.memory.snapshot('test')
        question = missing_detail_question(state, 'feature', 'negative_feedback')
        self.assertEqual(question.question['target_slot'], 'use_case')
        later = replace(state, turn=7)
        self.assertEqual(missing_detail_question(later, 'feature', 'negative_feedback').question['target_slot'], 'use_case')

    def test_aliases_match_titles_without_taxonomy(self):
        for title in ('Black tshirt', 'Black t-shirt', 'Black t shirt', 'Black tee', 'Black tees'):
            self.assertTrue(category_matches({'title': title}, 't-shirt')[0], title)
        self.assertFalse(category_matches({'title': 'Black dress shirt'}, 't-shirt')[0])

    def test_category_alias_spellings(self):
        for name in ('tshirt', 'tshirts', 't-shirt', 't-shirts', 'tee', 'tees', 't shirt'):
            parsed = self.agent.router.understand_turn(f'black {name} around $30')
            self.assertTrue(any(u.slot == 'category' and u.values == ('t-shirt',) for u in parsed.slot_updates), name)
