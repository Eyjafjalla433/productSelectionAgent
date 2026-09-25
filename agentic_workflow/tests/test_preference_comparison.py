import unittest

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.control_intent import parse_control_intent
from mvp.preference_comparison import compare_preferences, decision_followup, resolve_comparison_reply
from mvp.server import AgentRuntime
from intent_router.turn_router import TurnIntentRouter


class PreferenceComparisonTests(unittest.TestCase):
    def test_ordered_fabric_and_fit_preferences_keep_fallbacks(self):
        for slot, preferred, fallback, titles, message in (
            ('material', 'cotton', 'polyester',
             ('Polyester T-shirt', 'Cotton T-shirt', 'T-shirt with unlisted fabric'),
             'I need a T-shirt; I prefer cotton, but polyester is okay'),
            ('style', 'regular fit', 'loose fit',
             ('Loose fit T-shirt', 'Regular fit T-shirt', 'T-shirt with unlisted fit'),
             'I need a T-shirt; I prefer regular fit, but loose fit is okay'),
        ):
            with self.subTest(slot=slot):
                def search(query, top_k):
                    return [{'product_id': str(i), 'score': 3 - i} for i in range(3)]

                def details(ids):
                    return [dict(product_id=key, found=True, title=titles[int(key)]) for key in ids]

                runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                             trace_enabled=True), orchestration_mode='adaptive')
                sid = runtime.new_session()['session_id']
                result = runtime.chat(sid, message)
                self.assertEqual(result['receipt']['ordered_preferences'][slot],
                                 {'preferred': preferred, 'also_acceptable': fallback})
                self.assertEqual([item['parent_asin'] for item in result['products']],
                                 ['1', '0', '2'])
                self.assertNotIn(slot, result['receipt']['hard'])
                recap = runtime.chat(sid, 'What are my preferences?')
                self.assertIn(f'{preferred} first; {fallback} also okay',
                              recap['assistant']['message'])
                undone = runtime.chat(sid, 'Undo')
                self.assertNotIn(slot, undone['receipt']['soft'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_blend_does_not_get_two_material_preference_bonuses(self):
        def search(query, top_k):
            return [{'product_id': key, 'score': 3 - index}
                    for index, key in enumerate(('poly', 'cotton', 'blend'))]

        def details(ids):
            titles = {'poly': 'Polyester T-shirt', 'cotton': 'Cotton T-shirt',
                      'blend': 'Cotton polyester blend T-shirt'}
            return [dict(product_id=key, found=True, title=titles[key]) for key in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a T-shirt; I prefer cotton, but polyester is okay')
        self.assertEqual([item['parent_asin'] for item in result['products']],
                         ['cotton', 'blend', 'poly'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def runtime(self):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 20 - i} for i in range(12)]

        def details(ids):
            return [dict(product_id=i, found=True, title=f'T-shirt option {i}') for i in ids]

        runtime = AgentRuntime(
            Agent(search_function=search, details_function=details, trace_enabled=True),
            orchestration_mode='adaptive')
        return runtime, calls

    def test_comparison_keeps_results_and_shortlist_unchanged(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a t-shirt')
        before = len(calls)
        reply = runtime.chat(sid, 'Which of these is better for me?')
        self.assertEqual(len(calls), before)
        self.assertEqual(reply['receipt']['pre_reason'], 'preference_comparison')
        self.assertEqual(len(reply['receipt']['preference_comparison']['rows']), 2)
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertEqual(reply['receipt']['selection_count'], 0)
        self.assertIsNone(reply['receipt']['preference_comparison']['tentative_winner_rank'])
        self.assertIn("can't confidently choose one", reply['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_buying_advice_names_verified_tradeoff_without_treating_unknown_as_absent(self):
        products = [
            {'rank': rank, 'parent_asin': str(rank), 'title': f'Black tee {rank}',
             'match': {'signals': [], 'hard_supported': 1}}
            for rank in (1, 2, 3)
        ]
        records = {
            '1': {'title': 'Black regular fit tee', 'color': 'black'},
            '2': {'title': 'Black graphic tee', 'color': 'black'},
            '3': {'title': 'Black 100% cotton tee', 'color': 'black'},
        }
        message, comparison = compare_preferences(products, records.get, decision_help=True)
        self.assertIsNone(comparison['tentative_winner_rank'])
        self.assertIn('regular fit for #1', message)
        self.assertIn('100% cotton for #3', message)
        self.assertIn('An unlisted detail is unknown, not a drawback.', message)
        question = comparison['question']
        self.assertEqual(resolve_comparison_reply('fabric', question)['value'], '100% cotton')
        self.assertEqual(resolve_comparison_reply('fit', question)['value'], 'regular fit')
        self.assertEqual(resolve_comparison_reply('#3', question)['slot'], 'material')
        self.assertEqual(resolve_comparison_reply('cotton, but under $30', question)['extra_message'], 'under $30')
        self.assertIsNone(resolve_comparison_reply('blue fabric', question))

    def test_buying_tradeoff_short_answer_is_saved_and_undoable(self):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 20 - i} for i in range(12)]

        def details(ids):
            return [dict(product_id=i, found=True,
                         title=('Black regular fit T-shirt' if i == '0' else
                                'Black 100% cotton T-shirt' if i == '2' else
                                'Black T-shirt')) for i in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black T-shirt')
        before = len(calls)
        advice = runtime.chat(sid, 'Which one should I buy?')
        self.assertEqual(len(calls), before)
        self.assertEqual(advice['receipt']['preference_comparison']['question']['kind'], 'decision_tradeoff')
        self.assertEqual(advice['receipt']['suggested_replies'],
                         ['regular fit', '100% cotton', 'Either is fine'])
        chosen = runtime.chat(sid, 'fabric')
        self.assertIn('cotton', str(chosen['receipt']['soft'].get('material', '')).lower())
        self.assertEqual(chosen['receipt']['preference_comparison_answer']['slot'], 'material')
        self.assertIn('Since 100% cotton matters to you, start with #',
                      chosen['assistant']['message'])
        self.assertIsNotNone(chosen['receipt']['decision_followup']['recommended_rank'])
        self.assertIn('2', [product['parent_asin'] for product in chosen['products']])
        undone = runtime.chat(sid, 'undo')
        self.assertNotIn('material', undone['receipt']['soft'])
        runtime.chat(sid, 'Which one should I buy?')
        declined = runtime.chat(sid, 'neither')
        self.assertNotIn('material', declined['receipt']['soft'])
        self.assertEqual(len(calls), before + 1)
        runtime.chat(sid, 'Which one should I buy?')
        combined = runtime.chat(sid, 'fabric, but under $30')
        self.assertEqual(combined['receipt']['hard'].get('price_max'), 30.0)
        self.assertIn('cotton', str(combined['receipt']['soft'].get('material', '')).lower())
        reverted = runtime.chat(sid, 'undo')
        self.assertNotIn('material', reverted['receipt']['soft'])
        self.assertNotIn('price_max', reverted['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_preference_recap_keeps_tradeoff_answerable_without_search(self):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 3-i} for i in range(3)]

        def details(ids):
            titles = ['Black regular fit T-shirt', 'Black T-shirt', 'Black 100% cotton T-shirt']
            return [dict(product_id=i, found=True, title=titles[int(i)]) for i in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black T-shirt')
        advice = runtime.chat(sid, 'Which one should I buy?')
        count = len(calls)
        for _ in range(2):
            recap = runtime.chat(sid, 'What are my preferences?')
            self.assertEqual(len(calls), count)
            self.assertEqual(recap['products'], advice['products'])
            self.assertEqual(recap['receipt']['suggested_replies'],
                             ['regular fit', '100% cotton', 'Either is fine'])
        answer = runtime.chat(sid, 'fabric')
        self.assertEqual(answer['receipt']['preference_comparison_answer']['slot'], 'material')
        self.assertIn('cotton', str(answer['receipt']['soft']['material']).lower())
        self.assertNotIn('material', runtime.chat(sid, 'Undo')['receipt']['soft'])
        runtime.chat(sid, 'Which one should I buy?')
        runtime.chat(sid, 'What are my preferences?')
        declined = runtime.chat(sid, 'Either is fine')
        self.assertEqual(declined['receipt']['pre_reason'], 'preference_comparison_skip')
        self.assertEqual(declined['receipt']['soft'], {})
        runtime.chat(sid, 'Which one should I buy?')
        runtime.chat(sid, 'What are my preferences?')
        runtime.chat(sid, 'Change to blue')
        unrelated = runtime.chat(sid, 'fabric')
        self.assertFalse(unrelated['receipt'].get('preference_comparison_answer'))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_product_question_detours_keep_tradeoff_but_edits_invalidate_it(self):
        for detour in ('How much is #2?', 'How much is #2? Is it pure cotton?'):
            with self.subTest(detour=detour):
                calls = []

                def search(query, top_k):
                    calls.append(query)
                    return [{'product_id': str(i), 'score': 3-i} for i in range(3)]

                def details(ids):
                    titles = ['Black regular fit T-shirt', 'Black T-shirt', 'Black 100% cotton T-shirt']
                    return [dict(product_id=i, found=True, title=titles[int(i)]) for i in ids]

                runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                             trace_enabled=True), orchestration_mode='adaptive')
                sid = runtime.new_session()['session_id']
                runtime.chat(sid, 'black T-shirt')
                advice = runtime.chat(sid, 'Which one should I buy?')
                count = len(calls)
                answer = runtime.chat(sid, detour)
                self.assertEqual(answer['products'], advice['products'])
                self.assertEqual(len(calls), count)
                self.assertIn('#2:', answer['assistant']['message'])
                self.assertEqual(answer['receipt']['suggested_replies'],
                                 ['regular fit', '100% cotton', 'Either is fine'])
                chosen = runtime.chat(sid, 'fabric')
                self.assertEqual(chosen['receipt']['preference_comparison_answer']['slot'], 'material')
                runtime.chat(sid, 'Undo')
                runtime.chat(sid, 'Which one should I buy?')
                runtime.chat(sid, 'How much is #2? Also change to blue')
                stale = runtime.chat(sid, 'fabric')
                self.assertFalse(stale['receipt'].get('preference_comparison_answer'))
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_decision_followup_does_not_pick_without_verified_facet(self):
        products = [{'rank': 1, 'parent_asin': 'a', 'title': 'Black tee'}]
        message, evidence = decision_followup(
            products, {'slot': 'material', 'value': '100% cotton'},
            lambda asin: {'title': 'Black tee'})
        self.assertIsNone(evidence['recommended_rank'])
        self.assertIn("can't verify", message)

    def test_cross_dimension_priority_correction_keeps_secondary_preference(self):
        router = TurnIntentRouter()
        for phrase in ('Actually, fit matters more than fabric',
                       'I care more about fit than fabric'):
            parsed = router.understand_turn(phrase)
            self.assertEqual([(update.slot, update.operation) for update in parsed.slot_updates],
                             [('material', 'demote_soft'), ('style', 'promote_soft')])
            self.assertEqual(parsed.decision_evidence['question_focus'], 'style')

        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black T-shirt, preferably cotton')
        changed = runtime.chat(sid, 'Actually, fit matters more than fabric')
        self.assertIn('material', changed['receipt']['soft'])
        self.assertEqual(changed['receipt']['soft_priority']['material'], 0.35)
        self.assertEqual(changed['receipt']['hard'].get('color'), 'black')
        self.assertIn('keep cotton as a secondary preference', changed['assistant']['message'])
        self.assertNotIn('must-have', changed['assistant']['message'])
        restored = runtime.chat(sid, 'undo')
        self.assertIn('material', restored['receipt']['soft'])
        self.assertEqual(restored['receipt']['soft_priority']['material'], 1.0)
        runtime.chat(sid, 'Fit matters more than fabric')
        runtime.chat(sid, 'I prefer regular fit')
        weighted = runtime.agent.memory.snapshot(sid).soft_preferences
        self.assertGreater(weighted['style'][0].weight, weighted['material'][0].weight)

        hard_sid = runtime.new_session()['session_id']
        runtime.chat(hard_sid, '100% cotton black T-shirt')
        protected = runtime.chat(hard_sid, 'Actually, fit matters more than fabric')
        self.assertIn('material', protected['receipt']['hard'])
        self.assertIn('I kept 100% cotton as a must-have', protected['assistant']['message'])
        relaxed = runtime.chat(hard_sid, '100% cotton is optional')
        self.assertNotIn('material', relaxed['receipt']['hard'])
        self.assertIn('material', relaxed['receipt']['soft'])

    def test_explicit_priority_beats_recency_of_existing_preferences(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black T-shirt, I prefer regular fit')
        for _ in range(8):
            runtime.chat(sid, 'thanks')
        runtime.chat(sid, 'I prefer cotton')
        changed = runtime.chat(sid, 'Fit matters more than fabric')
        self.assertIn('style', changed['receipt']['soft'])
        self.assertIn('material', changed['receipt']['soft'])
        self.assertEqual(changed['receipt']['soft_priority_turn']['style'], 11)
        self.assertEqual(changed['receipt']['state_evidence']['soft']['style']['source_turn'], 1)
        weighted = runtime.agent.memory.snapshot(sid).soft_preferences
        self.assertGreater(weighted['style'][0].weight, weighted['material'][0].weight)
        restored = runtime.chat(sid, 'undo')
        self.assertIsNone(restored['receipt']['soft_priority_turn']['style'])

    def test_priority_changes_visible_order_for_two_supported_tradeoffs(self):
        def search(query, top_k):
            return [{'product_id': 'fabric', 'score': 1.0},
                    {'product_id': 'fit', 'score': 1.0}]

        def details(ids):
            titles = {'fabric': 'Black 100% cotton slim fit T-shirt',
                      'fit': 'Black regular fit polyester T-shirt'}
            return [dict(product_id=asin, found=True, title=titles[asin]) for asin in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black T-shirt, I prefer regular fit')
        for _ in range(8):
            runtime.chat(sid, 'thanks')
        before = runtime.chat(sid, 'I prefer 100% cotton')
        after = runtime.chat(sid, 'Fit matters more than fabric')
        self.assertEqual(before['products'][0]['parent_asin'], 'fabric')
        self.assertEqual(after['products'][0]['parent_asin'], 'fit')
        explained = runtime.chat(sid, 'Why is #1 first?')
        self.assertIn('fit', explained['assistant']['message'].lower())
        self.assertIn('fabric', explained['assistant']['message'].lower())
        self.assertIn({'slot': 'style', 'value': 'regular fit'},
                      explained['receipt']['rank_explanation']['supported_preferences'])
        weights = explained['receipt']['rank_explanation']['preference_weights']
        self.assertGreater(weights['fit'], weights['fabric'])
        self.assertNotIn('Its exact position comes from the current relevance ranking',
                         explained['assistant']['message'])
        self.assertEqual([row['parent_asin'] for row in explained['products']],
                         [row['parent_asin'] for row in after['products']])
        comparison = runtime.chat(sid, 'Which is better, #1 or #2?')
        self.assertEqual(comparison['receipt']['preference_comparison']['tentative_winner_rank'], 1)
        self.assertEqual(comparison['receipt']['preference_comparison']['winner_basis'], 'explicit_priority')
        self.assertIn('higher-priority', comparison['assistant']['message'])
        self.assertIn('preferences for fit', comparison['assistant']['message'])
        self.assertEqual(comparison['receipt']['preference_comparison']['decision_slots'], ['style'])
        self.assertEqual(comparison['products'], after['products'])
        runtime.chat(sid, 'Undo')
        unweighted = runtime.chat(sid, 'Which is better, #1 or #2?')
        self.assertIsNone(unweighted['receipt']['preference_comparison']['tentative_winner_rank'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_material_preference_does_not_reward_negated_listing(self):
        def search(query, top_k):
            return [{'product_id': 'not', 'score': 1.0},
                    {'product_id': 'yes', 'score': 1.0}]

        def details(ids):
            titles = {'not': 'Black T-shirt, not cotton',
                      'yes': 'Black cotton T-shirt'}
            return [dict(product_id=asin, found=True, title=titles[asin]) for asin in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'black T-shirt, preferably cotton')
        self.assertEqual([row['parent_asin'] for row in result['products']], ['yes', 'not'])
        denied_signal = next(signal for signal in result['products'][1]['match']['signals']
                             if signal['slot'] == 'material' and signal['tier'] == 'soft')
        self.assertNotEqual(denied_signal['status'], 'supported')
        explained = runtime.chat(sid, 'Why is #1 first?')
        self.assertIn('catalog-supported preferences affect this order',
                      explained['assistant']['message'])
        self.assertIn({'slot': 'material', 'value': 'cotton'},
                      explained['receipt']['rank_explanation']['supported_preferences'])
        hard_sid = runtime.new_session()['session_id']
        strict = runtime.chat(hard_sid, 'black cotton T-shirt')
        self.assertEqual([row['parent_asin'] for row in strict['products']], ['yes'])

    def test_buying_advice_uses_current_page_without_searching_again(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a T-shirt')
        before = len(calls)
        reply = runtime.chat(sid, 'Which one should I buy?')
        self.assertEqual(len(calls), before)
        self.assertEqual(reply['receipt']['pre_reason'], 'preference_comparison')
        self.assertEqual(len(reply['receipt']['preference_comparison']['rows']), 3)
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertIn("wouldn't call one a clear winner", reply['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicit_and_invalid_ranks(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 't-shirt')
        before = len(calls)
        reply = runtime.chat(sid, 'Which is better, #1 or #2?')
        self.assertEqual([row['rank'] for row in reply['receipt']['preference_comparison']['rows']], [1, 2])
        invalid = runtime.chat(sid, 'Which is better, #1 or #99?')
        self.assertIsNone(invalid['receipt']['preference_comparison'])
        self.assertIn('#99', invalid['assistant']['message'])
        self.assertEqual(len(calls), before)

    def test_tentative_winner_requires_supported_strict_superset(self):
        products = [
            {'rank': 1, 'parent_asin': 'a', 'title': 'A', 'match': {'hard_supported': 1,
                'signals': [{'slot': 'color', 'value': 'black', 'tier': 'soft', 'status': 'supported'}]}},
            {'rank': 2, 'parent_asin': 'b', 'title': 'B', 'match': {'hard_supported': 1, 'signals': []}},
        ]
        message, metadata = compare_preferences(products, lambda asin: {'parent_asin': asin})
        self.assertEqual(metadata['tentative_winner_rank'], 1)
        self.assertIn("I'd start with #1", message)
        self.assertIn('not a guarantee of quality or fit', message)
        self.assertIn('matches your black preference', message)
        products[1]['match']['signals'] = [
            {'slot': 'fit', 'value': 'regular', 'tier': 'soft', 'status': 'supported'}]
        message, metadata = compare_preferences(products, lambda asin: {'parent_asin': asin})
        self.assertIsNone(metadata['tentative_winner_rank'])

    def test_priority_comparison_does_not_count_duplicates_or_override_hard_evidence(self):
        def signal(slot, value):
            return {'slot': slot, 'value': value, 'tier': 'soft', 'status': 'supported'}

        products = [
            {'rank': 1, 'parent_asin': 'a', 'title': 'A', 'match': {'hard_supported': 1,
                'signals': [signal('style', 'regular fit')]}},
            {'rank': 2, 'parent_asin': 'b', 'title': 'B', 'match': {'hard_supported': 1,
                'signals': [signal('material', 'cotton'), signal('material', 'cotton'),
                            signal('color', 'black')]}},
        ]
        priorities = {'style': 1.5, 'material': .35, 'color': .35}
        _, data = compare_preferences(products, lambda _: {}, priorities=priorities)
        self.assertEqual(data['tentative_winner_rank'], 1)
        self.assertEqual(data['priority_scores'], {'a': [1, 0], 'b': [0, 2]})
        products[0]['match']['signals'].append(signal('color', 'black'))
        _, shared = compare_preferences(products, lambda _: {}, priorities={**priorities, 'color': 1.5})
        self.assertEqual(shared['decision_slots'], ['style'])
        products[0]['match']['signals'].pop()
        products[0]['match']['hard_supported'] = 0
        _, data = compare_preferences(products, lambda _: {}, priorities=priorities)
        self.assertIsNone(data['tentative_winner_rank'])
        products[0]['match']['hard_supported'] = 1
        _, data = compare_preferences(products, lambda _: {}, priorities={'style': 1, 'material': 1, 'color': 1})
        self.assertIsNone(data['tentative_winner_rank'])
        products[0]['match']['signals'][0]['status'] = 'unknown'
        _, data = compare_preferences(products, lambda _: {}, priorities=priorities)
        self.assertNotEqual(data['tentative_winner_rank'], 1)

    def test_only_verified_difference_prompts_optional_preference(self):
        products = [
            {'rank': 1, 'parent_asin': 'a', 'title': 'A', 'match': {'signals': []}},
            {'rank': 2, 'parent_asin': 'b', 'title': 'B', 'match': {'signals': []}},
        ]
        records = {
            'a': {'title': 'Black slim fit t-shirt', 'color': 'black'},
            'b': {'title': 'Blue regular fit t-shirt', 'color': 'blue'},
        }
        message, metadata = compare_preferences(products, records.get)
        self.assertIn('Do you prefer either', message)
        self.assertIn('#1 is slim fit and #2 is regular fit', metadata['next_question'])
        records['b'] = {'title': 'Another black slim fit t-shirt', 'color': 'black'}
        message, metadata = compare_preferences(products, records.get)
        self.assertIsNone(metadata['next_question'])

    def test_feature_and_preference_evidence_is_not_repeated_in_comparison(self):
        products = [{'rank': rank, 'parent_asin': str(rank), 'title': 'Black tee',
                     'match': {'signals': [{'slot': 'color', 'value': 'black',
                                            'tier': 'soft', 'status': 'supported'}]}}
                    for rank in (1, 2)]
        records = {str(rank): {'title': 'Black tee', 'color': 'black'} for rank in (1, 2)}
        text, evidence = compare_preferences(products, records.get)
        self.assertEqual(text.count('black'), 1)
        self.assertIn('matches your black preference', text)
        self.assertEqual(evidence['rows'][0]['verified_facets']['color'], 'black')
        products[1]['match']['signals'] = []
        records['2'] = {'title': 'T-shirt'}
        text, evidence = compare_preferences(products, records.get)
        self.assertEqual(text.count('black'), 1)
        self.assertIn('#1: matches your black preference', text)
        self.assertEqual(evidence['tentative_winner_rank'], 1)

    def test_shared_facts_and_missing_prices_do_not_repeat_as_two_caveats(self):
        products = [
            {'rank': 1, 'parent_asin': 'a', 'title': 'A', 'match': {'signals': []}},
            {'rank': 2, 'parent_asin': 'b', 'title': 'B', 'match': {'signals': []}},
        ]
        records = {
            'a': {'title': 'Black slim fit tee', 'color': 'black'},
            'b': {'title': 'Another black slim fit tee', 'color': 'black'},
        }
        message, metadata = compare_preferences(products, records.get)
        self.assertIn("can't confidently", message)
        self.assertNotIn('match signals', message)
        self.assertNotIn('no verified fit, color, fabric, or price difference', message)
        self.assertNotIn('Prices are missing', message)
        self.assertIsNone(metadata['next_question'])

    def test_short_answer_to_fit_difference_is_applied_and_undoable(self):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 50-i} for i in range(12)]

        def details(ids):
            return [dict(product_id=i, found=True,
                         title='Black slim fit t-shirt' if int(i) % 2 == 0
                         else 'Blue regular fit t-shirt',
                         color='black' if int(i) % 2 == 0 else 'blue') for i in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 't-shirt')
        comparison = runtime.chat(sid, 'Which is better, #1 or #2?')
        self.assertIsNotNone(comparison['receipt']['preference_comparison']['next_question'])
        answer = runtime.chat(sid, 'the first one')
        self.assertEqual(answer['receipt']['soft'].get('style'), ['slim fit'])
        self.assertGreater(len(calls), 1)
        undone = runtime.chat(sid, 'undo')
        self.assertNotIn('style', undone['receipt']['soft'])
        runtime.chat(sid, 'Which is better, #1 or #2?')
        mixed = runtime.chat(sid, 'the first one, but under $30')
        self.assertEqual(mixed['receipt']['soft'].get('style'), ['slim fit'])
        self.assertEqual(mixed['receipt']['hard'].get('price_max'), 30.0)
        self.assertEqual(mixed['receipt']['preference_comparison_answer']['value'], 'slim fit')
        restored = runtime.chat(sid, 'undo')
        self.assertNotIn('style', restored['receipt']['soft'])
        self.assertNotIn('price_max', restored['receipt']['hard'])
        runtime.chat(sid, 'Which is better, #1 or #2?')
        named = runtime.chat(sid, 'I prefer slim fit, but under $30')
        self.assertEqual(named['receipt']['preference_comparison_answer']['value'], 'slim fit')
        self.assertEqual(named['receipt']['soft'].get('style'), ['slim fit'])
        self.assertEqual(named['receipt']['hard'].get('price_max'), 30.0)
        restored = runtime.chat(sid, 'undo')
        self.assertNotIn('style', restored['receipt']['soft'])
        self.assertNotIn('price_max', restored['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_named_color_answer_is_a_preference_not_an_exclusive_filter(self):
        def search(query, top_k):
            return [{'product_id': value, 'score': 2-i} for i, value in enumerate(('black', 'blue'))]

        def details(ids):
            return [dict(product_id=value, found=True, title=f'{value} T-shirt', color=value)
                    for value in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'T-shirt')
        question = runtime.chat(sid, 'Which is better, #1 or #2?')
        self.assertEqual(question['receipt']['preference_comparison']['question']['slot'], 'color')
        self.assertEqual(question['receipt']['suggested_replies'], ['black', 'blue', 'Either is fine'])
        reply = runtime.chat(sid, 'blue, please')
        self.assertEqual(reply['receipt']['soft']['color'], ['blue'])
        self.assertNotIn('color', reply['receipt']['hard'])
        self.assertEqual({p['parent_asin'] for p in reply['products']}, {'black', 'blue'})
        self.assertEqual(reply['receipt']['preference_comparison_answer']['value'], 'blue')
        self.assertNotIn('color', runtime.chat(sid, 'Undo')['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_parser_does_not_capture_normal_requirement(self):
        self.assertEqual(parse_control_intent('Which of these is better for me?').action,
                         'preference_compare')
        self.assertEqual(parse_control_intent('Between #1 and #2, which is better?').action,
                         'preference_compare')
        for prompt in ('Which one should I buy?', 'Which product should I choose?',
                       'What would you recommend?'):
            self.assertEqual(parse_control_intent(prompt).action, 'preference_compare')
        self.assertIsNone(parse_control_intent('I want a better black t-shirt'))

    def test_comparison_reference_only_accepts_a_bare_immediate_choice(self):
        question = {'slot': 'color', 'options': [
            {'rank': 1, 'value': 'black', 'parent_asin': 'a'},
            {'rank': 2, 'value': 'blue', 'parent_asin': 'b'},
        ]}
        self.assertEqual(resolve_comparison_reply('the second one', question)['value'], 'blue')
        self.assertEqual(resolve_comparison_reply('#1', question)['value'], 'black')
        reversed_question = {**question, 'options': list(reversed(question['options']))}
        self.assertEqual(resolve_comparison_reply('the first one', reversed_question)['value'], 'blue')
        self.assertEqual(resolve_comparison_reply('#1', reversed_question)['value'], 'black')
        self.assertEqual(resolve_comparison_reply('the first one, but under $30', question)['extra_message'],
                         'under $30')
        self.assertIsNone(resolve_comparison_reply('I want the first one in red', question))
        self.assertIsNone(resolve_comparison_reply('undo', question))
        self.assertIsNone(resolve_comparison_reply('the first one', None))
        for text in ('blue', 'I prefer blue', 'blue, please'):
            self.assertEqual(resolve_comparison_reply(text, question)['parent_asin'], 'b')
        for text in ('not blue', 'blue or black', 'Is blue available?', 'blue instead of black'):
            self.assertIsNone(resolve_comparison_reply(text, question))
        duplicate = {**question, 'options': [question['options'][0], dict(question['options'][1], value='black')]}
        self.assertIsNone(resolve_comparison_reply('black', duplicate))

    def test_comparison_reply_uses_displayed_rank_not_option_position(self):
        question = {'slot': 'color', 'options': [
            {'rank': 10, 'value': 'blue', 'parent_asin': 'ten'},
            {'rank': 3, 'value': 'black', 'parent_asin': 'three'},
        ]}
        for text, asin in (('#10', 'ten'), ('#3', 'three'), ('the first one', 'ten'),
                           ('the second one', 'three')):
            self.assertEqual(resolve_comparison_reply(text, question)['parent_asin'], asin)
        self.assertEqual(resolve_comparison_reply('#10, but under $30', question)['extra_message'], 'under $30')
        for text in ('#1', '#2', '#0', '#11', '#3 or #10'):
            self.assertIsNone(resolve_comparison_reply(text, question))

    def test_later_rank_preference_reply_survives_recap_and_is_undoable(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 20-i} for i in range(10)]

        def details(ids):
            return [dict(product_id=i, found=True,
                         title=('Slim fit' if int(i) % 2 == 0 else 'Regular fit') + ' T-shirt')
                    for i in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'T-shirt')
        runtime.chat(sid, 'Which is better, #4 or #3?')
        runtime.chat(sid, 'What are my preferences?')
        reply = runtime.chat(sid, '#4')
        self.assertEqual(reply['receipt']['soft']['style'], ['regular fit'])
        self.assertEqual(reply['receipt']['preference_comparison_answer']['parent_asin'],
                         first['products'][3]['parent_asin'])
        self.assertEqual(reply['selection_state']['selected_asins'], [])
        restored = runtime.chat(sid, 'Undo')
        self.assertNotIn('style', restored['receipt']['soft'])
        self.assertEqual(restored['products'], first['products'])
        runtime.chat(sid, 'Which is better, #4 or #3?')
        corrected = runtime.chat(sid, 'Not the first one—the second')
        self.assertEqual(corrected['receipt']['soft']['style'], ['slim fit'])
        self.assertEqual(corrected['receipt']['preference_comparison_answer']['parent_asin'],
                         first['products'][2]['parent_asin'])
        self.assertEqual(corrected['selection_state']['selected_asins'], [])
        self.assertEqual(corrected['receipt']['excluded'], {})
        self.assertFalse(corrected['receipt']['rejected_asins'])
        self.assertNotIn('style', runtime.chat(sid, 'Undo')['receipt']['soft'])
        runtime.chat(sid, 'Which is better, #4 or #3?')
        polite = runtime.chat(sid, 'the second, please')
        self.assertEqual(polite['receipt']['soft']['style'], ['slim fit'])
        self.assertIsNone(polite['receipt']['preference_comparison_answer']['extra_message'])
        self.assertEqual(polite['selection_state']['selected_asins'], [])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicit_comparison_correction_requires_two_different_known_choices(self):
        question = {'slot': 'color', 'options': [
            {'rank': 4, 'value': 'blue', 'parent_asin': 'blue-item'},
            {'rank': 3, 'value': 'black', 'parent_asin': 'black-item'},
        ]}
        for text in ('not blue, black', 'not #4 but #3', 'not the first one—the second'):
            result = resolve_comparison_reply(text, question)
            self.assertEqual(result['value'], 'black')
            self.assertIsNone(result['extra_message'])
        for text in ('not blue', 'not blue, blue', 'not #99, #3', 'not blue, black or red',
                     'not blue, maybe black', 'not blue, black and under $30'):
            self.assertIsNone(resolve_comparison_reply(text, question))
        tradeoff = {'kind': 'decision_tradeoff', 'options': [
            {'rank': 4, 'value': 'regular fit', 'parent_asin': 'fit-item', 'facet': 'fit', 'slot': 'style'},
            {'rank': 3, 'value': '100% cotton', 'parent_asin': 'fabric-item', 'facet': 'fabric', 'slot': 'material'},
        ]}
        correction = resolve_comparison_reply('not fit, fabric', tradeoff)
        self.assertEqual(correction['slot'], 'material')
        self.assertEqual(correction['value'], '100% cotton')
        self.assertEqual(correction['kind'], 'decision_tradeoff')
        self.assertEqual(resolve_comparison_reply('the fabric, please', tradeoff)['slot'], 'material')
        for question in (question, tradeoff):
            for wording in ('the second, please', 'I prefer the second one', '#3 please'):
                with self.subTest(wording=wording, kind=question.get('kind')):
                    reply = resolve_comparison_reply(wording, question)
                    self.assertEqual(reply['parent_asin'], question['options'][1]['parent_asin'])
                    self.assertIsNone(reply['extra_message'])
            for wording in ('not the second', 'maybe the second', 'the first or second',
                            'Is the second better?'):
                self.assertIsNone(resolve_comparison_reply(wording, question))

    def test_declining_comparison_keeps_new_budget_and_supports_undo(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 3-i} for i in range(3)]

        def details(ids):
            return [dict(product_id=i, found=True, price=20 + 10*int(i),
                         title=('Slim fit' if i == '0' else 'Regular fit') + ' T-shirt') for i in ids]

        for message in ('Either is fine, but under $30', 'No preference and under $30',
                        'Neither, under $30'):
            with self.subTest(message=message):
                runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                             trace_enabled=True), orchestration_mode='adaptive')
                sid = runtime.new_session()['session_id']
                initial = runtime.chat(sid, 'T-shirt')
                runtime.chat(sid, 'Which is better, #1 or #2?')
                reply = runtime.chat(sid, message)
                self.assertEqual(reply['receipt']['hard']['price_max'], 30.0)
                self.assertNotIn('style', reply['receipt']['soft'])
                self.assertFalse(reply['receipt']['excluded'])
                self.assertFalse(reply['receipt']['rejected_asins'])
                self.assertIn('comparison_declined', reply['receipt'])
                self.assertEqual(runtime.audit(sid)['turns'][-1]['user_message'], message)
                undone = runtime.chat(sid, 'Undo')
                self.assertNotIn('price_max', undone['receipt']['hard'])
                self.assertEqual(undone['products'], initial['products'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_declining_optional_comparison_question_keeps_current_list(self):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 50-i} for i in range(12)]

        def details(ids):
            return [dict(product_id=i, found=True,
                         title='Black slim fit t-shirt' if int(i) % 2 == 0
                         else 'Blue regular fit t-shirt',
                         color='black' if int(i) % 2 == 0 else 'blue') for i in ids]

        for refusal in ('neither', 'no preference', "I'm open to either",
                        "I don't mind either", 'No strong preference'):
            with self.subTest(refusal=refusal):
                runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                             trace_enabled=True), orchestration_mode='adaptive')
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 't-shirt')
                comparison = runtime.chat(sid, 'Which is better, #1 or #2?')
                self.assertIsNotNone(comparison['receipt']['preference_comparison']['next_question'])
                before = len(calls)
                reply = runtime.chat(sid, refusal)
                self.assertEqual(len(calls), before)
                self.assertEqual(reply['receipt']['pre_reason'], 'preference_comparison_skip')
                self.assertEqual(reply['receipt']['hard'], first['receipt']['hard'])
                self.assertEqual(reply['receipt']['soft'], first['receipt']['soft'])
                self.assertEqual([item['parent_asin'] for item in reply['products']],
                                 [item['parent_asin'] for item in comparison['products']])
                self.assertIsNone(reply['assistant']['ask_attribute'])
                self.assertIsNone(runtime.agent.memory.pending[sid])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
