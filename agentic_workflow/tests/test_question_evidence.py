import unittest
from types import SimpleNamespace

import agentic_workflow
from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.shadow_policy import product_facets, shadow_question_board
from shopping_agent.policy import PostRetrievalPolicy
from shopping_agent.retrieval import material_matches, style_matches


class QuestionEvidenceTests(unittest.TestCase):
    def test_question_utility_uses_expected_reduction_not_fixed_pool_size(self):
        def run(pool_size):
            def search(query, top_k):
                return [{'product_id': str(i), 'score': pool_size - i}
                        for i in range(pool_size)]

            def details(ids):
                return [dict(product_id=key, found=True,
                             title=('Black' if int(key) < pool_size // 2 else 'White') + ' T-shirt')
                        for key in ids]

            runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                         trace_enabled=True), orchestration_mode='adaptive')
            sid = runtime.new_session()['session_id']
            return runtime.chat(sid, 'I need a T-shirt')

        useful = run(18)
        self.assertEqual(useful['receipt']['post_reason'], 'candidate_information_gain')
        self.assertEqual(useful['receipt']['question']['target_slot'], 'color')
        self.assertEqual(set(useful['receipt']['question']['options']), {'black', 'white'})
        self.assertTrue(useful['products'])

        manageable = run(12)
        self.assertEqual(manageable['receipt']['post_reason'], 'manageable_candidate_pool')
        self.assertIsNone(manageable['receipt']['question'])
        self.assertTrue(manageable['products'])

    def test_decision_help_without_useful_question_offers_comparison(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 12 - i} for i in range(12)]

        def details(ids):
            return [dict(product_id=key, found=True,
                         title=('Black' if int(key) < 6 else 'White') + ' T-shirt')
                    for key in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        browsing = runtime.chat(sid, 'I am just browsing T-shirts')
        self.assertIsNone(browsing['receipt']['question'])
        guided = runtime.chat(sid, 'Help me choose')
        self.assertEqual(guided['receipt']['post_reason'], 'manageable_candidate_pool')
        self.assertIsNone(guided['receipt']['question'])
        self.assertIn('compare #1 and #2', guided['assistant']['message'])
        self.assertTrue(guided['products'])
        self.assertEqual([item['parent_asin'] for item in guided['products']],
                         [item['parent_asin'] for item in browsing['products']])

    def test_style_questions_share_filter_evidence(self):
        examples = [
            ({'title': 'Fitted tee'}, 'slim fit'),
            ({'title': 'Relaxed-fit tee'}, 'loose fit'),
            ({'title': 'Oversized tee'}, 'loose fit'),
            ({'title': 'Fitted tee', 'details': {'Fit Type': 'Regular Fit'}}, 'regular fit'),
            ({'title': 'Fitted tee', 'details': {'Fit Type': 'Loose Fit'}}, 'loose fit'),
            ({'title': 'Not very fitted tee'}, ''),
            ({'title': 'Fitted tee and loose fit tee'}, ''),
        ]
        for product, expected in examples:
            with self.subTest(product=product):
                self.assertEqual(product_facets(product)['style'], expected)
                if expected:
                    self.assertTrue(style_matches(product, expected))

    def test_fit_question_counts_match_filter_groups(self):
        products = [{'title': 'Fitted cotton tee', 'details': {'Fit Type': fit}}
                    for fit in ['Loose Fit'] * 10 + ['Regular Fit'] * 10]
        question = next(q for q in shadow_question_board(products, turns_left=None)
                        if q['attribute'] == 'style')
        self.assertEqual({o['value']: o['count'] for o in question['options']},
                         {'loose fit': 10, 'regular fit': 10})
        self.assertEqual(question['coverage'], 1)
        for option in question['options']:
            self.assertEqual(option['count'], sum(style_matches(p, option['value']) for p in products))

    def test_runtime_asks_about_fit_aliases_without_blocking_results(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 30-i} for i in range(24)]
        def details(ids):
            return [dict(product_id=key, found=True,
                         title=('Fitted' if int(key) < 12 else 'Relaxed-fit') + ' cotton tshirt') for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a tshirt')
        self.assertTrue(result['products'])
        question = result['receipt']['question']
        self.assertEqual(question['target_slot'], 'style')
        self.assertEqual(set(question['options']), {'slim fit', 'loose fit'})
        reply = runtime.chat(sid, '必须宽松')
        self.assertEqual(reply['receipt']['hard']['style'], 'loose fit')
        self.assertTrue(reply['products'])
        self.assertTrue(all(int(p['parent_asin']) >= 12 for p in reply['products']))
        self.assertTrue(all('Loose fit' in p['shopper_notes']['feature'] for p in reply['products']))

    def budget_decision(self, *, scope_start=0, count=3, empty=False, focus=None, pool_size=24):
        state = SimpleNamespace(
            session_id='budget-test', turn=9,
            suggestions={'requirements_changed': True, 'clarification_streak': 0,
                         'max_turns': None, 'question_scope_start': scope_start,
                         'question_focus': focus},
            hard_constraints={'category': 't-shirt'}, soft_preferences={}, exclusions={},
            asked_questions=[{'turn': i + 1, 'target_slot': 'style',
                              'evidence': {'expected_reduction': .5}}
                             for i in range(count)])
        retrieval = SimpleNamespace(candidates=[SimpleNamespace(product={
            'title': 'cotton tshirt', 'color': 'black' if i % 2 else 'white'
        }) for i in range(pool_size)])
        ranking = SimpleNamespace(ranked_candidates=[] if empty else [object()])
        return PostRetrievalPolicy(max_questions=None).decide(state, retrieval, ranking)

    def test_results_turn_does_not_refill_target_question_budget(self):
        decision = self.budget_decision()
        self.assertEqual(decision.action, 'recommend')
        self.assertEqual(decision.reason, 'target_clarification_budget')
        self.assertIsNone(decision.question)

    def test_under_budget_still_asks_useful_question(self):
        self.assertEqual(self.budget_decision(count=2).action, 'recommend_and_clarify')

    def test_prior_optional_questions_raise_the_information_bar(self):
        self.assertEqual(self.budget_decision(count=0, pool_size=18).action,
                         'recommend_and_clarify')
        later = self.budget_decision(count=2, pool_size=18)
        self.assertEqual(later.action, 'recommend')
        self.assertEqual(later.reason, 'manageable_candidate_pool')

    def test_new_target_gets_its_own_question_budget(self):
        self.assertEqual(self.budget_decision(scope_start=8).action, 'recommend_and_clarify')

    def test_optional_question_budget_does_not_hide_empty_result_recovery(self):
        self.assertEqual(self.budget_decision(empty=True).reason, 'empty_eligible_pool')

    def test_named_focus_respects_target_budget_but_not_for_empty_results(self):
        self.assertEqual(self.budget_decision(focus='color').reason, 'focus_attention_limit')
        self.assertEqual(self.budget_decision(focus='color', empty=True).reason, 'empty_eligible_pool')
        self.assertEqual(self.budget_decision(focus='color', scope_start=8).action,
                         'recommend_and_clarify')

    def test_live_policy_only_offers_current_variant_colors(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 30-i} for i in range(20)]
        def details(ids):
            return [dict(product_id=key, found=True, title='Black cotton tshirt',
                         color='blue' if int(key) < 10 else 'red') for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        question = runtime.chat(sid, 'I need a tshirt')['receipt']['question']
        self.assertEqual(question['target_slot'], 'color')
        self.assertEqual(set(question['options']), {'blue', 'red'})
        reply = runtime.chat(sid, 'blue please')
        self.assertTrue(reply['products'])
        self.assertTrue(all(int(p['parent_asin']) < 10 for p in reply['products']))

    def test_question_color_uses_variant_not_other_options(self):
        product = {'title': 'Black cotton tshirt', 'details': {'Color': 'Blue'},
                   'features': ['Also available in black and red']}
        self.assertEqual(product_facets(product)['color'], 'blue')
        self.assertEqual(product_facets({'details': {'Color': 'Navy'}})['color'], 'blue')
        self.assertEqual(product_facets({'details': {'Color': 'Grey'}})['color'], 'gray')

    def test_ambiguous_facets_do_not_inflate_question_coverage(self):
        product = {'title': 'Black and white cotton polyester tshirt for work and travel'}
        facets = product_facets(product)
        self.assertEqual(facets['color'], '')
        self.assertEqual(facets['material'], '')
        self.assertEqual(facets['use_case'], '')
        self.assertEqual(product_facets({'title': 'Not cotton tshirt'})['material'], '')

    def test_material_question_counts_follow_filter_evidence(self):
        products = ([{'title': 'Cotton-free polyester T-shirt'} for _ in range(10)]
                    + [{'title': 'Cotton T-shirt'} for _ in range(10)])
        self.assertEqual(product_facets(products[0])['material'], 'polyester')
        self.assertFalse(material_matches(products[0], 'cotton'))
        material = next(row for row in shadow_question_board(products, turns_left=None)
                        if row['attribute'] == 'material')
        self.assertEqual({option['value']: option['count'] for option in material['options']},
                         {'polyester': 10, 'cotton': 10})
        for option in material['options']:
            self.assertEqual(option['count'],
                             sum(material_matches(product, option['value']) for product in products))

    def test_runtime_fabric_question_does_not_offer_cotton_free_as_cotton(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 30 - i} for i in range(24)]

        def details(ids):
            return [dict(product_id=key, found=True,
                         title=('Cotton-free polyester' if int(key) < 12 else 'Cotton')
                         + ' T-shirt') for key in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        opening = runtime.chat(sid, 'I need a T-shirt')
        question = opening['receipt']['question']
        self.assertEqual(question['target_slot'], 'material')
        self.assertEqual(set(question['options']), {'cotton', 'polyester'})
        answer = runtime.chat(sid, 'cotton please')
        self.assertTrue(answer['products'])
        self.assertTrue(all(int(item['parent_asin']) >= 12 for item in answer['products']))

    def test_candidate_counts_and_options_follow_verified_color(self):
        products = [dict(title='Black tshirt', details={'Color': color}) for color in ['blue']*10 + ['red']*10]
        board = shadow_question_board(products, turns_left=None)
        color = next(row for row in board if row['attribute'] == 'color')
        self.assertEqual({o['value']: o['count'] for o in color['options']}, {'blue': 10, 'red': 10})
        self.assertEqual(color['coverage'], 1)
        self.assertEqual(color['expected_reduction'], .5)
        products += [dict(title='Blue red tshirt') for _ in range(20)]
        color = next(row for row in shadow_question_board(products, turns_left=None) if row['attribute'] == 'color')
        self.assertEqual(color['coverage'], .5)
