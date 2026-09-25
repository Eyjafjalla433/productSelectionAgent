import unittest

import agentic_workflow
from mvp.explanations import explain_product, product_advice
from mvp.preference_comparison import compare_preferences, decision_followup
from mvp.server import AgentRuntime
from mvp.shopping_guide import preference_tradeoff


class PreferenceExplanationTests(unittest.TestCase):
    def test_breathability_support_requires_noncontradictory_claims(self):
        for product in ({'title': 'Non-breathable T-shirt'},
                        {'title': 'T-shirt, not very breathable'},
                        {'title': 'Breathable T-shirt', 'description': ['Not breathable in humid conditions.']}):
            for tier in ('soft', 'hard'):
                with self.subTest(product=product, tier=tier):
                    match = explain_product(product, {tier: {'feature': ['breathable']}})
                    self.assertNotEqual(match['signals'][0]['status'], 'supported')
                    self.assertEqual(match['signals'][0]['matched_values'], [])
                    self.assertFalse(product_advice(product, match)['pros'])
            excluded = explain_product(product, {'excluded': {'feature': ['breathable']}})
            self.assertEqual(excluded['signals'][0]['conflicting_values'], [])
        positive = explain_product({'title': 'Breathable T-shirt'}, {'soft': {'feature': ['breathable']}})
        self.assertEqual(positive['signals'][0]['matched_values'], ['breathable'])

    def test_required_breathability_does_not_admit_a_negated_listing(self):
        runtime = AgentRuntime(agentic_workflow.Agent(
            search_function=lambda query, top_k: [{'product_id': 'no', 'score': 2}, {'product_id': 'yes', 'score': 1}],
            details_function=lambda ids: [{'product_id': asin, 'found': True,
                'title': ('Non-breathable' if asin == 'no' else 'Breathable') + ' black T-shirt'} for asin in ids],
            trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        reply = runtime.chat(sid, 'black T-shirt, must be breathable')
        self.assertIn('feature', reply['receipt']['hard'])
        self.assertEqual([p['parent_asin'] for p in reply['products']], ['yes'])

    def test_tradeoff_answer_and_new_avoidance_are_respected_in_same_turn(self):
        titles = ['Black regular fit T-shirt', 'Black T-shirt', 'Black 100% cotton baggy T-shirt']
        runtime = AgentRuntime(agentic_workflow.Agent(
            search_function=lambda query, top_k: [{'product_id': str(i), 'score': 3-i} for i in range(3)],
            details_function=lambda ids: [{'product_id': i, 'found': True, 'title': titles[int(i)]}
                                          for i in ids], trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black T-shirt')
        advice = runtime.chat(sid, 'Which one should I buy?')
        self.assertEqual(advice['receipt']['preference_comparison']['question']['kind'], 'decision_tradeoff')
        answer = runtime.chat(sid, 'fabric, but nothing too baggy')
        self.assertEqual(answer['receipt']['soft']['fit_avoid'], ['baggy'])
        self.assertIn('100% cotton', answer['receipt']['soft']['material'])
        self.assertIsNone(answer['receipt']['decision_followup']['recommended_rank'])
        self.assertIn('baggy', answer['assistant']['message'])
        self.assertFalse(answer['selection_state']['selected_asins'])
        self.assertFalse(answer['receipt']['rejected_asins'])
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('fit_avoid', undone['receipt']['soft'])
        self.assertNotIn('material', undone['receipt']['soft'])

    def test_followup_pick_keeps_existing_avoidance_preference(self):
        receipt = {'soft': {'material': ['100% cotton'], 'fit_avoid': ['baggy']}}
        records = {'a': {'title': '100% cotton baggy T-shirt'},
                   'b': {'title': '100% cotton regular fit T-shirt'}}
        products = [{'rank': rank, 'parent_asin': asin, 'title': record['title'],
                     'match': explain_product(record, receipt)}
                    for rank, (asin, record) in enumerate(records.items(), 1)]
        choice = {'slot': 'material', 'value': '100% cotton'}
        text, data = decision_followup(products, choice, records.get)
        self.assertEqual(data['recommended_rank'], 2)
        self.assertEqual(data['conflicting_ranks'], [1])
        self.assertEqual(data['verified_matches'], 2)
        self.assertIn('baggy', text)
        self.assertNotIn("isn't a unique winner", text)
        text, data = decision_followup(products[:1], choice, records.get)
        self.assertIsNone(data['recommended_rank'])
        self.assertEqual(data['verified_matches'], 1)
        self.assertIn('baggy', text)
        self.assertNotIn("can't verify", text)

    def test_conflict_warnings_only_name_observed_values_not_all_exclusions(self):
        receipt = {'excluded': {'color': ['red', 'blue']},
                   'soft': {'fit_avoid': ['baggy', 'tight']}}
        record = {'title': 'Red baggy T-shirt', 'color': 'red'}
        match = explain_product(record, receipt)
        signals = {signal['slot']: signal for signal in match['signals']}
        self.assertEqual(signals['color']['conflicting_values'], ['red'])
        self.assertEqual(signals['fit_avoid']['conflicting_values'], ['baggy'])
        self.assertEqual(signals['color']['value'], ['red', 'blue'])
        claims = ' '.join(point['text'] for point in product_advice(record, match)['cons'])
        self.assertIn('red', claims)
        self.assertIn('baggy', claims)
        self.assertNotIn('blue', claims)
        self.assertNotIn('tight', claims)
        products = [{'rank': 1, 'parent_asin': 'a', 'title': record['title'], 'match': match},
                    {'rank': 2, 'parent_asin': 'b', 'title': 'T-shirt',
                     'match': explain_product({'title': 'T-shirt'}, receipt)}]
        text, metadata = compare_preferences(products, lambda asin: record if asin == 'a' else {})
        self.assertIn('lists red, which you excluded', text)
        self.assertNotIn('blue', text)
        self.assertNotIn('tight', text)
        self.assertFalse(metadata['rows'][1]['known_conflicts'])

    def test_comparison_does_not_hide_known_fit_conflict_behind_a_material_match(self):
        receipt = {'soft': {'material': ['cotton'], 'fit_avoid': ['baggy']}}
        records = {'a': {'title': 'Cotton baggy T-shirt'}, 'b': {'title': 'T-shirt'}}
        products = [{'rank': rank, 'parent_asin': asin, 'title': record['title'],
                     'match': explain_product(record, receipt)}
                    for rank, (asin, record) in enumerate(records.items(), 1)]
        text, comparison = compare_preferences(products, records.get)
        self.assertIsNone(comparison['tentative_winner_rank'])
        self.assertIn('baggy, which you preferred to avoid', text)
        self.assertIn('cotton', text)
        self.assertNotIn("I'd start with", text)
        self.assertFalse(comparison['rows'][1]['known_conflicts'])
        self.assertIsNone(comparison['next_question'])
        cleared = {'soft': {'material': ['cotton']}}
        for product in products:
            product['match'] = explain_product(records[product['parent_asin']], cleared)
        _, comparison = compare_preferences(products, records.get)
        self.assertEqual(comparison['tentative_winner_rank'], 1)

    def test_hard_alternatives_preserve_filter_but_explain_only_verified_size(self):
        receipt = {'hard': {'category': 't-shirt', 'size': ['m', 'l'], 'color': ['black', 'white']}}
        product = {'title': 'Black T-shirt size M', 'color': 'black'}
        result = explain_product(product, receipt)
        signals = {signal['slot']: signal for signal in result['signals']}
        self.assertEqual(signals['size']['value'], ['m', 'l'])
        self.assertEqual(signals['size']['matched_values'], ['m'])
        self.assertEqual(signals['color']['matched_values'], ['black'])
        self.assertEqual(result['hard_supported'], 3)
        claims = [point['text'] for point in product_advice(product, result)['pros']]
        self.assertIn('Supports size: m', claims)
        self.assertNotIn('Supports size: m l', claims)
        self.assertNotIn('Supports color: black white', claims)
        self.assertEqual(receipt['hard']['size'], ['m', 'l'])

    def test_rank_explanation_names_only_the_matched_color_alternative(self):
        runtime = AgentRuntime(agentic_workflow.Agent(
            search_function=lambda query, top_k: [{'product_id': 'a', 'score': 1}],
            details_function=lambda ids: [{'product_id': 'a', 'found': True,
                                          'title': 'Black T-shirt', 'color': 'black'}],
            trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'T-shirt, I prefer black, but white is okay')
        reply = runtime.chat(sid, 'Why is #1 first?')
        self.assertIn({'slot': 'color', 'value': 'black'},
                      reply['receipt']['rank_explanation']['supported_preferences'])
        self.assertNotIn({'slot': 'color', 'value': 'black, white'},
                         reply['receipt']['rank_explanation']['supported_preferences'])

    def test_accepted_alternatives_only_claim_the_values_verified_for_each_product(self):
        receipt = {'soft': {'color': ['black', 'white'], 'material': ['cotton', 'polyester']}}
        records = {'a': {'title': 'Black cotton T-shirt', 'color': 'black'},
                   'b': {'title': 'White polyester T-shirt', 'color': 'white'}}
        products = []
        for rank, (asin, record) in enumerate(records.items(), 1):
            match = explain_product(record, receipt)
            products.append({'rank': rank, 'parent_asin': asin, 'title': record['title'], 'match': match})
            supported = {signal['slot']: signal['matched_values'] for signal in match['signals']}
            self.assertEqual(supported['color'], ['black'] if asin == 'a' else ['white'])
            advice = product_advice(record, match)
            claims = ' '.join(point['text'] for point in advice['pros'])
            self.assertNotIn('white' if asin == 'a' else 'black', claims)
            self.assertNotIn('polyester' if asin == 'a' else 'cotton', claims)
        message, comparison = compare_preferences(products, records.get)
        self.assertEqual(comparison['rows'][0]['supported_preferences'], ['black', 'cotton'])
        self.assertEqual(comparison['rows'][1]['supported_preferences'], ['white', 'polyester'])
        self.assertIsNone(comparison['tentative_winner_rank'])
        self.assertNotIn('Shared listing details:', message)
        self.assertEqual(receipt['soft']['color'], ['black', 'white'])

    def test_first_choice_comparison_requires_unique_verified_support(self):
        receipt = {'soft': {'color': ['black', 'white']}}
        ordered = {'color': {'preferred': 'black', 'also_acceptable': 'white'}}
        products = [{'rank': rank, 'parent_asin': str(rank), 'title': 'Black tee',
                     'match': explain_product({'title': 'Black tee'}, receipt)} for rank in (1, 2)]
        _, result = compare_preferences(products, lambda _: {}, ordered_preferences=ordered)
        self.assertIsNone(result['tentative_winner_rank'])
        products[1]['match'] = explain_product({'title': 'White tee'}, receipt)
        _, result = compare_preferences(products, lambda _: {}, ordered_preferences=ordered)
        self.assertEqual(result['tentative_winner_rank'], 1)
        reverse = {'color': {'preferred': 'white', 'also_acceptable': 'black'}}
        _, result = compare_preferences(products, lambda _: {}, ordered_preferences=reverse)
        self.assertEqual(result['tentative_winner_rank'], 2)
        products[1]['match']['hard_supported'] = 1
        _, result = compare_preferences(products, lambda _: {}, ordered_preferences=ordered)
        self.assertIsNone(result['tentative_winner_rank'])

    def product(self, title, receipt):
        return {'match': explain_product({'title': title}, receipt)}

    def test_pure_cotton_evidence_matches_filter_not_token_overlap(self):
        receipt = {'hard': {'material': '100% cotton'}}
        pure = self.product('Pure cotton tshirt', receipt)
        self.assertEqual(pure['match']['signals'][0]['status'], 'supported')
        blend = self.product('100% cotton tee; heather polyester blend', receipt)
        self.assertNotEqual(blend['match']['signals'][0]['status'], 'supported')
        soft = self.product('100% cotton tee; heather polyester blend', {'soft': {'material': ['100% cotton']}})
        self.assertIn('could not confirm', preference_tradeoff([soft]))

    def test_budget_number_in_title_is_not_price_evidence(self):
        product = self.product('30/1 cotton tshirt', {'soft': {'budget_target': [30]}})
        self.assertEqual(product['match']['signals'][0]['status'], 'unknown')
        self.assertEqual(preference_tradeoff([product]), '')

    def test_missing_preference_is_not_claimed_as_product_defect(self):
        receipt = {'soft': {'color': ['black']}}
        unknown = self.product('Cotton tshirt', receipt)
        text = preference_tradeoff([unknown])
        self.assertIn('black', text)
        self.assertIn('could not confirm', text)
        self.assertNotIn('不是black', text)
        matched = self.product('Black cotton tshirt', receipt)
        self.assertEqual(preference_tradeoff([matched]), '')
        self.assertIn('1 of these', preference_tradeoff([matched, unknown]))
        self.assertIn('alternatives', preference_tradeoff([unknown], 'en'))

    def test_no_unsolicited_caveats_without_preferences(self):
        product = self.product('Cotton tshirt', {'hard': {'material': 'cotton'}})
        self.assertEqual(preference_tradeoff([product]), '')
