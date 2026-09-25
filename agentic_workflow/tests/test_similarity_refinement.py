import unittest
from pathlib import Path

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.server import AgentRuntime
from mvp.similarity import parse_facet_reply


class SimilarityRefinementTests(unittest.TestCase):
    def test_hide_one_item_and_ask_for_more_like_another_in_one_turn(self):
        for message in ('Hide #2 and show me more like #1',
                        'Show me more like #1 and reject #2',
                        "I don't like #2 and show me more like #1"):
            with self.subTest(message=message):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'tshirt')
                anchor = first['products'][0]['parent_asin']
                hidden = first['products'][1]['parent_asin']
                mixed = runtime.chat(sid, message)
                self.assertEqual(mixed['receipt']['similarity_reference']['parent_asin'], anchor)
                self.assertIn(hidden, mixed['receipt']['rejected_asins'])
                self.assertEqual(mixed['receipt']['question']['target_slot'],
                                 'similarity_attribute')
                self.assertEqual(mixed['products'], first['products'])
                self.assertIn('#2', mixed['assistant']['message'])
                refined = runtime.chat(sid, 'color')
                self.assertNotIn(hidden, [item['parent_asin'] for item in refined['products']])
                restored = runtime.chat(sid, 'undo rejection')
                self.assertNotIn(hidden, restored['receipt']['rejected_asins'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_invalid_similarity_reference_does_not_hide_other_item(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        for message in ('Hide #2 and show me more like #99',
                        'Hide #1 and show me more like #1'):
            with self.subTest(message=message):
                reply = runtime.chat(sid, message)
                self.assertEqual(reply['products'], first['products'])
                self.assertEqual(reply['receipt']['rejected_asins'], [])
                self.assertIsNone(runtime.sessions[sid].pending_similarity)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_undo_rejection_keeps_unanswered_similarity_choice_visible(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        hidden = first['products'][1]['parent_asin']
        mixed = runtime.chat(sid, 'Hide #2 and show me more like #1')
        pending = runtime.sessions[sid].pending_similarity.copy()
        self.assertIn(hidden, mixed['receipt']['rejected_asins'])
        undone = runtime.chat(sid, 'undo rejection')
        self.assertNotIn(hidden, undone['receipt']['rejected_asins'])
        self.assertEqual(undone['receipt']['question']['options'],
                         mixed['receipt']['question']['options'])
        self.assertEqual(runtime.sessions[sid].pending_similarity, pending)
        self.assertEqual(undone['products'], first['products'])
        redone = runtime.chat(sid, 'redo rejection')
        self.assertIn(hidden, redone['receipt']['rejected_asins'])
        self.assertEqual(redone['receipt']['question']['options'], list(pending['facets']))
        undone_again = runtime.chat(sid, 'undo rejection')
        self.assertNotIn(hidden, undone_again['receipt']['rejected_asins'])
        self.assertEqual(undone_again['receipt']['question']['options'],
                         mixed['receipt']['question']['options'])
        answered = runtime.chat(sid, 'color')
        self.assertEqual(answered['receipt']['similarity_refinement']['parent_asin'],
                         first['products'][0]['parent_asin'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_product_detail_does_not_discard_open_similarity_choice(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        question = runtime.chat(sid, 'Show me more like #1')
        pending = runtime.sessions[sid].pending_similarity.copy()
        detail = runtime.chat(sid, 'How much does #2 cost?')
        self.assertIn("can't confirm its cost", detail['assistant']['message'])
        self.assertEqual(detail['products'], first['products'])
        self.assertEqual(detail['receipt']['question'], question['receipt']['question'])
        self.assertEqual(detail['receipt']['similarity_reference'],
                         question['receipt']['similarity_reference'])
        self.assertEqual(runtime.sessions[sid].pending_similarity, pending)
        self.assertNotIn('2_retrieval',
                         [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']])
        answer = runtime.chat(sid, 'color')
        self.assertEqual(answer['receipt']['similarity_refinement']['parent_asin'],
                         first['products'][0]['parent_asin'])
        self.assertEqual(answer['receipt']['soft']['color'], ['black'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_more_like_item_but_cheaper_keeps_both_parts_of_request(self):
        catalog = Path(__file__).resolve().parents[1] / 'mvp' / 'demo_data' / 'catalog.jsonl'
        runtime = AgentRuntime(Agent(catalog_path=catalog,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        reference = max(first['products'], key=lambda item: item['price'])
        anchor = reference['parent_asin']
        mixed = runtime.chat(sid, f"Show me more like #{reference['rank']} but cheaper")
        self.assertEqual(mixed['receipt']['similarity_mixed_request']['parent_asin'], anchor)
        self.assertLess(mixed['receipt']['hard']['price_max'], reference['price'])
        self.assertTrue(mixed['products'])
        self.assertTrue(all(item['price'] < reference['price'] for item in mixed['products']))
        self.assertIn('catalog price', mixed['assistant']['message'])
        self.assertNotIn('I used your new search detail', mixed['assistant']['message'])
        self.assertNotIn('unsupported similarity claim', mixed['assistant']['message'])
        self.assertEqual(mixed['receipt']['question']['target_slot'], 'similarity_attribute')
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('price_max', undone['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unpriced_similar_cheaper_request_does_not_guess_a_price(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        mixed = runtime.chat(sid, 'Show me more like #1 but cheaper')
        self.assertEqual(mixed['products'], first['products'])
        self.assertNotIn('price_max', mixed['receipt']['hard'])
        self.assertIn('no catalog price', mixed['assistant']['message'])
        self.assertEqual(mixed['receipt']['question']['target_slot'], 'similarity_attribute')
        self.assertEqual(mixed['selection_state']['selected_asins'], [])
        self.assertFalse(any(event['stage'].startswith('2_retrieval')
                             for event in runtime.agent.get_trace(sid, mixed['turn'])['events']))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_similar_cheaper_and_color_apply_together_when_priced(self):
        catalog = Path(__file__).resolve().parents[1] / 'mvp' / 'demo_data' / 'catalog.jsonl'
        runtime = AgentRuntime(Agent(catalog_path=catalog, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        reference = max(first['products'], key=lambda item: item['price'])
        reply = runtime.chat(sid, f"Show me more like #{reference['rank']} but cheaper and blue")
        self.assertEqual(reply['receipt']['similarity_mixed_request']['parent_asin'],
                         reference['parent_asin'])
        self.assertEqual(reply['receipt']['hard']['color'], 'blue')
        self.assertLess(reply['receipt']['hard']['price_max'], reference['price'])
        self.assertTrue(reply['products'])
        self.assertTrue(all(item['price'] < reference['price'] for item in reply['products']))
        self.assertNotIn('I used your new search detail', reply['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unpriced_similar_cheaper_still_applies_explicit_color(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        reply = runtime.chat(sid, 'Show me more like #1 but cheaper and blue')
        self.assertEqual(reply['receipt']['hard']['color'], 'blue')
        self.assertNotIn('price_max', reply['receipt']['hard'])
        self.assertEqual(reply['receipt']['similarity_mixed_request']['parent_asin'],
                         first['products'][0]['parent_asin'])
        self.assertIn('no catalog price', reply['assistant']['message'])
        self.assertTrue(reply['products'])
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('color', undone['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_similar_cheaper_does_not_swallow_a_second_shortlist_action(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        reply = runtime.chat(sid, 'Show me more like #1 but cheaper and select #2')
        self.assertEqual(reply['products'], first['products'])
        self.assertEqual(reply['selection_state']['selected_asins'], [])
        self.assertNotIn('price_max', reply['receipt']['hard'])
        self.assertIn("can't safely apply this combination", reply['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_similar_cheaper_and_explicit_cap_use_the_stricter_limit(self):
        catalog = Path(__file__).resolve().parents[1] / 'mvp' / 'demo_data' / 'catalog.jsonl'
        for requested_cap in (45, 60):
            with self.subTest(requested_cap=requested_cap):
                runtime = AgentRuntime(Agent(catalog_path=catalog, trace_enabled=True),
                                       orchestration_mode='adaptive')
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'I need a dress')
                reference = max(first['products'], key=lambda item: item['price'])
                reply = runtime.chat(
                    sid, f"Show me more like #{reference['rank']} but cheaper and under "
                         f"${requested_cap} and blue")
                expected_cap = min(requested_cap, reference['price'] - .01)
                self.assertAlmostEqual(reply['receipt']['hard']['price_max'], expected_cap)
                self.assertEqual(reply['receipt']['hard']['color'], 'blue')
                self.assertEqual(reply['receipt']['similarity_mixed_request']['parent_asin'],
                                 reference['parent_asin'])
                self.assertTrue(all(item['price'] <= expected_cap for item in reply['products']))
                undone = runtime.chat(sid, 'Undo')
                self.assertNotIn('price_max', undone['receipt']['hard'])
                self.assertNotIn('color', undone['receipt']['hard'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unpriced_relative_request_keeps_explicit_cap_without_claiming_match(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        reply = runtime.chat(sid, 'Show me more like #1 but cheaper and under $25 and blue')
        self.assertEqual(reply['receipt']['hard']['price_max'], 25)
        self.assertEqual(reply['receipt']['hard']['color'], 'blue')
        self.assertEqual(reply['receipt']['similarity_mixed_request']['parent_asin'],
                         first['products'][0]['parent_asin'])
        self.assertIn('no catalog price', reply['assistant']['message'])
        self.assertFalse(reply['products'])
        self.assertEqual(reply['assistant']['ask_attribute'], 'budget')
        self.assertNotIn('I found possible products, but this catalog has no prices',
                         reply['assistant']['message'])
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('price_max', undone['receipt']['hard'])
        self.assertNotIn('color', undone['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_relative_request_with_only_an_explicit_cap_stays_grounded(self):
        catalog = Path(__file__).resolve().parents[1] / 'mvp' / 'demo_data' / 'catalog.jsonl'
        runtime = AgentRuntime(Agent(catalog_path=catalog, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        reference = max(first['products'], key=lambda item: item['price'])
        reply = runtime.chat(sid, f"Show me more like #{reference['rank']} but cheaper and under $45")
        self.assertEqual(reply['receipt']['hard']['price_max'], 45)
        self.assertEqual(reply['receipt']['question']['target_slot'], 'similarity_attribute')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

        unpriced = self.runtime()
        other_sid = unpriced.new_session()['session_id']
        unpriced.chat(other_sid, 'tshirt')
        limited = unpriced.chat(other_sid, 'Show me more like #1 but cheaper and under $25')
        self.assertEqual(limited['receipt']['hard']['price_max'], 25)
        self.assertFalse(limited['products'])
        self.assertIn('no catalog price', limited['assistant']['message'])
        self.assertEqual(verify_audit(unpriced.audit(other_sid)), [])

    def test_unpriced_similar_cheaper_accepts_two_new_details_in_one_turn(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'tshirt')
        reply = runtime.chat(sid, 'Show me more like #1 but cheaper and blue and regular fit')
        self.assertEqual(reply['receipt']['hard']['color'], 'blue')
        self.assertEqual(reply['receipt']['soft']['style'], ['regular fit'])
        self.assertNotIn('price_max', reply['receipt']['hard'])
        self.assertIn('no catalog price', reply['assistant']['message'])
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('color', undone['receipt']['hard'])
        self.assertNotIn('style', undone['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_out_of_range_similar_cheaper_reference_keeps_search_unchanged(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        reply = runtime.chat(sid, 'Show me more like #99 but cheaper')
        self.assertEqual(reply['products'], first['products'])
        self.assertEqual(reply['selection_state']['selected_asins'], [])
        self.assertIn('one rank from the current list', reply['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    @staticmethod
    def runtime(first_title='Black 100% cotton slim fit tshirt'):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 20-i} for i in range(12)]

        def details(ids):
            return [dict(product_id=i, found=True,
                         title=first_title if i == '0' else 'Blue regular fit polyester tshirt')
                    for i in ids]

        return AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                            orchestration_mode='adaptive')

    def test_asks_for_grounded_facet_then_accepts_two_at_once(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        anchor = first['products'][0]['parent_asin']
        question = runtime.chat(sid, 'Show me more like #1')
        self.assertEqual([p['parent_asin'] for p in question['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertEqual(question['receipt']['question']['options'], ['fit', 'color', 'fabric'])
        self.assertEqual(question['receipt']['similarity_reference']['parent_asin'], anchor)
        self.assertNotIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, 2)['events']])
        answer = runtime.chat(sid, 'fit and color')
        self.assertEqual(answer['receipt']['similarity_refinement']['parent_asin'], anchor)
        self.assertEqual(answer['receipt']['similarity_refinement']['chosen_facets'], ['fit', 'color'])
        self.assertEqual(answer['receipt']['soft']['style'], ['slim fit'])
        self.assertEqual(answer['receipt']['soft']['color'], ['black'])
        self.assertIn('slim fit, black', answer['assistant']['message'])
        self.assertIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, 3)['events']])
        self.assertEqual(runtime.audit(sid)['turns'][2]['user_message'], 'fit and color')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unverified_anchor_does_not_invent_similarity_facts(self):
        runtime = self.runtime(first_title='Plain tshirt')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        reply = runtime.chat(sid, 'More like #1')
        self.assertIsNone(reply['receipt']['question'])
        self.assertIsNone(reply['receipt']['similarity_reference'])
        self.assertIn("doesn't clearly verify", reply['assistant']['message'])
        self.assertEqual(reply['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_new_request_abandons_pending_similarity_question(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        changed = runtime.chat(sid, 'Actually, blue instead')
        self.assertIsNone(changed['receipt'].get('similarity_refinement'))
        self.assertEqual(changed['receipt']['hard']['color'], 'blue')
        self.assertIsNone(runtime.sessions[sid].pending_similarity)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_does_not_ask_for_a_color_already_in_requirements(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black cotton tshirt')
        reply = runtime.chat(sid, 'More like #1')
        self.assertNotIn('color', reply['receipt']['question']['options'])
        self.assertIn('fit', reply['receipt']['question']['options'])
        self.assertIn('fabric', reply['receipt']['question']['options'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_already_known_anchor_details_browse_without_another_question(self):
        runtime = self.runtime(first_title='Black tshirt')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        reply = runtime.chat(sid, 'More like #1')
        self.assertIsNone(reply['receipt']['question'])
        self.assertEqual(reply['receipt']['similarity_browse']['parent_asin'], first['products'][0]['parent_asin'])
        self.assertEqual(reply['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(reply['receipt']['soft'], first['receipt']['soft'])
        self.assertEqual(reply['receipt']['new_product_count'], 0)
        self.assertIn("couldn't find a new verified option", reply['assistant']['message'])
        self.assertIn('2_retrieval', [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_already_known_details_can_surface_fresh_options(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 30-i} for i in range(24)]

        def details(ids):
            return [dict(product_id=i, found=True, title=f'Black tshirt design {i}') for i in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        reply = runtime.chat(sid, 'More like #1')
        self.assertIsNone(reply['receipt']['question'])
        self.assertGreater(reply['receipt']['new_product_count'], 0)
        self.assertEqual(reply['receipt']['hard'], first['receipt']['hard'])
        self.assertIn('more catalog options', reply['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_facet_answer_can_include_a_new_independent_requirement(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        reply = runtime.chat(sid, 'fit and blue')
        self.assertEqual(reply['receipt']['similarity_refinement']['chosen_facets'], ['fit'])
        self.assertEqual(reply['receipt']['similarity_refinement']['additional_requirements'], 'blue')
        self.assertEqual(reply['receipt']['soft']['style'], ['slim fit'])
        self.assertEqual(reply['receipt']['hard']['color'], 'blue')
        undone = runtime.chat(sid, 'undo')
        self.assertNotIn('style', undone['receipt']['soft'])
        self.assertNotIn('color', undone['receipt']['hard'])
        self.assertEqual([p['parent_asin'] for p in undone['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertEqual(undone['receipt']['focused_product_id'], first['products'][0]['parent_asin'])
        followup = runtime.chat(sid, 'How much is it?')
        self.assertIn('#1:', followup['assistant']['message'])
        self.assertEqual(followup['receipt']['pre_reason'], 'product_detail')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_natural_reply_selects_catalog_color(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        reply = runtime.chat(sid, 'I like the black color')
        self.assertEqual(reply['receipt']['similarity_refinement']['chosen_facets'], ['color'])
        self.assertEqual(reply['receipt']['soft']['color'], ['black'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_all_or_both_are_bounded_by_offered_facet_count(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        reply = runtime.chat(sid, 'I like all three')
        self.assertEqual(reply['receipt']['similarity_refinement']['chosen_facets'],
                         ['fit', 'color', 'fabric'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

        facets = {'fit': {'value': 'slim fit'}, 'color': {'value': 'black'},
                  'fabric': {'value': '100% cotton'}}
        self.assertIsNone(parse_facet_reply('both', facets))
        self.assertIsNone(parse_facet_reply("I don't like the fit", facets))
        self.assertEqual(parse_facet_reply('both', dict(list(facets.items())[:2])),
                         (('fit', 'color'), None))

    def test_ambiguous_pair_keeps_reference_then_accepts_precise_reply(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        repair = runtime.chat(sid, 'both')
        self.assertEqual(repair['receipt']['question']['options'], ['fit', 'color', 'fabric'])
        self.assertEqual(repair['receipt']['similarity_reference']['parent_asin'],
                         first['products'][0]['parent_asin'])
        self.assertEqual(repair['products'], first['products'])
        self.assertNotIn('2_retrieval', [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']])
        refined = runtime.chat(sid, 'fit and color')
        self.assertEqual(refined['receipt']['similarity_refinement']['chosen_facets'], ['fit', 'color'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_negative_fit_reply_excludes_verified_fit_and_can_undo(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        refined = runtime.chat(sid, "I don't like the fit")
        self.assertEqual(refined['receipt']['similarity_exclusion']['value'], 'slim fit')
        self.assertEqual(refined['receipt']['excluded']['style'], ['slim fit'])
        self.assertNotIn('style', refined['receipt']['soft'])
        self.assertNotIn(first['products'][0]['parent_asin'],
                         [product['parent_asin'] for product in refined['products']])
        undone = runtime.chat(sid, 'undo')
        self.assertNotIn('style', undone['receipt']['excluded'])
        self.assertEqual([product['parent_asin'] for product in undone['products']],
                         [product['parent_asin'] for product in first['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_fabric_dislike_does_not_overgeneralize_to_all_cotton(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        repair = runtime.chat(sid, "I don't like the fabric")
        self.assertEqual(repair['receipt']['question']['options'], ['fit', 'color'])
        self.assertNotIn('material', repair['receipt']['excluded'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_negative_color_reply_uses_catalog_color_not_a_guess(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        reply = runtime.chat(sid, "I don't like the color")
        self.assertEqual(reply['receipt']['similarity_exclusion']['value'], 'black')
        self.assertEqual(reply['receipt']['excluded']['color'], ['black'])
        self.assertTrue(reply['products'])
        self.assertTrue(all('black' not in product['title'].lower() for product in reply['products']))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicitly_lifting_disliked_fit_does_not_make_it_a_preference(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        excluded = runtime.chat(sid, "I don't like the fit")
        self.assertEqual(excluded['receipt']['excluded']['style'], ['slim fit'])
        lifted = runtime.chat(sid, 'No longer avoid slim fit')
        self.assertNotIn('style', lifted['receipt']['excluded'])
        self.assertNotIn('style', lifted['receipt']['soft'])
        self.assertNotIn('style', lifted['receipt']['hard'])
        self.assertTrue(lifted['products'])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['excluded']['style'], ['slim fit'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_preference_recap_preserves_open_reference_question(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        question = runtime.chat(sid, 'More like #1')
        recap = runtime.chat(sid, 'What are my preferences?')
        self.assertIn('black color', recap['assistant']['message'])
        self.assertIn("You're shopping for t-shirt", recap['assistant']['message'])
        self.assertEqual(recap['receipt']['preference_summary']['hard']['color'], 'black')
        self.assertEqual(recap['receipt']['question']['options'], question['receipt']['question']['options'])
        self.assertIsNotNone(runtime.sessions[sid].pending_similarity)
        self.assertNotIn('2_retrieval', [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']])
        answer = runtime.chat(sid, 'fit')
        self.assertEqual(answer['receipt']['similarity_refinement']['chosen_facets'], ['fit'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_preference_recap_without_search_has_no_invented_memory(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        recap = runtime.chat(sid, 'What have I told you so far?')
        self.assertIn("haven't set any search requirements", recap['assistant']['message'])
        self.assertEqual(recap['products'], [])
        self.assertEqual(recap['receipt']['preference_summary'], {'hard': {}, 'soft': {}, 'excluded': {}})
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_repeated_ambiguous_pair_stops_reasking(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'More like #1')
        runtime.chat(sid, 'both')
        stopped = runtime.chat(sid, 'both')
        self.assertIsNone(stopped['receipt']['question'])
        self.assertIsNone(runtime.sessions[sid].pending_similarity)
        self.assertEqual(stopped['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(stopped['products'], first['products'])
        self.assertIn('No need to settle', stopped['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_similarity_and_new_color_in_one_turn(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        anchor = first['products'][0]['parent_asin']
        mixed = runtime.chat(sid, 'Show me more like #1 but in blue')
        self.assertEqual(mixed['receipt']['hard']['color'], 'blue')
        self.assertEqual(mixed['receipt']['similarity_mixed_request']['parent_asin'], anchor)
        self.assertEqual(mixed['receipt']['question']['options'], ['fit', 'fabric'])
        self.assertEqual(mixed['selection_state']['selected_asins'], [])
        self.assertIn('2_retrieval', [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']])
        refined = runtime.chat(sid, 'I like the fit')
        self.assertEqual(refined['receipt']['similarity_refinement']['parent_asin'], anchor)
        self.assertEqual(refined['receipt']['hard']['color'], 'blue')
        self.assertEqual(refined['receipt']['soft']['style'], ['slim fit'])
        undone = runtime.chat(sid, 'undo')
        self.assertEqual(undone['receipt']['hard']['color'], 'blue')
        self.assertNotIn('style', undone['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
