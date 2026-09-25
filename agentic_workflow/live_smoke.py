"""Check actual search_tool inference and the full selection lifecycle."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from .preflight import check_search_tool
from .runtime import create_runtime
from mvp.audit import verify_audit
from shopping_agent.retrieval import style_matches


def tool_hashes():
    root = Path(__file__).resolve().parents[1] / 'search_tool'
    hashes = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            hashes[str(path.relative_to(root))] = digest.hexdigest()
    return hashes


def check_conversation(runtime):
    """Exercise recovery against actual search results, not injected candidates."""
    sid = runtime.new_session()['session_id']
    records = []

    def chat(message):
        result = runtime.chat(sid, message)
        assert not runtime.agent.errors, runtime.agent.errors
        assert not verify_audit(runtime.audit(sid))
        assert not re.search(r'[\u3400-\u9fff]', result['assistant']['message'])
        assert not re.search(r'[\u3400-\u9fff]', json.dumps(result['shopping_guide'], ensure_ascii=False))
        for product in result['products']:
            source = runtime.agent.get_catalog_product(product['parent_asin'])
            assert source, product['parent_asin']
            source_text = ' '.join((source.get('title', ''), str(source.get('features', [])),
                                    str(source.get('details', {})))).lower()
            for attribute in ('color', 'material'):
                expected = result['receipt']['hard'].get(attribute)
                if isinstance(expected, str):
                    assert re.search(r'\b' + re.escape(expected) + r'\b', source_text), (product['parent_asin'], attribute, expected)
            assert source.get('price') is None  # This dataset cannot verify the $30 target.
        records.append({'turn': result['turn'], 'message': message,
                        'products': len(result['products']),
                        'top_title': result['products'][0]['title'] if result['products'] else None,
                        'reason': result['receipt'].get('post_reason') or result['receipt'].get('pre_reason')})
        return result

    first = chat('I need a black cotton tshirt around $30')
    assert first['products']
    assert first['receipt']['hard']['color'] == 'black'
    assert 'price_max' not in first['receipt']['hard']
    assert first['receipt']['post_reason'] == 'candidate_information_gain'
    assert 'all in the mix' in first['assistant']['message']
    assert 'These options differ in' not in first['assistant']['message']
    rank_reply = chat('Why is #1 first?')
    assert rank_reply['receipt']['pre_reason'] == 'rank_explanation'
    assert rank_reply['receipt']['rank_explanation']['parent_asin'] == first['products'][0]['parent_asin']
    assert rank_reply['receipt']['rank_explanation']['budget_unverified']
    assert 'budget fit remains unverified' in rank_reply['assistant']['message']
    assert [p['parent_asin'] for p in rank_reply['products']] == [p['parent_asin'] for p in first['products']]
    assert not any(e['stage'].startswith('2_retrieval') for e in runtime.agent.get_trace(sid, rank_reply['turn'])['events'])
    reviews_reply = chat('Which one has the best reviews?')
    assert reviews_reply['receipt']['pre_reason'] == 'review_comparison'
    assert reviews_reply['receipt']['review_comparison']['rated_count'] == 0
    assert "can't identify a best-reviewed one" in reviews_reply['assistant']['message']
    assert [p['parent_asin'] for p in reviews_reply['products']] == [p['parent_asin'] for p in first['products']]
    assert not any(e['stage'].startswith('2_retrieval') for e in runtime.agent.get_trace(sid, reviews_reply['turn'])['events'])
    detail_reply = chat('What material is the second one made of?')
    assert any(phrase in detail_reply['assistant']['message'] for phrase in
               ('The listing says:', 'The listing specifies', 'The listing describes it as pure cotton.'))
    assert [p['parent_asin'] for p in detail_reply['products']] == [p['parent_asin'] for p in first['products']]
    price_reply = chat('How much does #2 cost?')
    assert 'does not list a price' in price_reply['assistant']['message']
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, price_reply['turn'])['events']]
    cheaper_reply = chat('Something cheaper, please')
    assert cheaper_reply['receipt']['pre_reason'] == 'price_comparison'
    assert cheaper_reply['receipt']['price_comparison']['priced_count'] == 0
    assert "can't tell which is cheaper" in cheaper_reply['assistant']['message']
    assert [p['parent_asin'] for p in cheaper_reply['products']] == [p['parent_asin'] for p in first['products']]
    assert not any(e['stage'].startswith('2_retrieval') for e in runtime.agent.get_trace(sid, cheaper_reply['turn'])['events'])
    size_and_detail = chat('Size M, and is #2 pure cotton?')
    assert size_and_detail['receipt']['hard']['size'] == 'm'
    assert size_and_detail['receipt']['compound_request']['detail_context']['parent_asin'] == first['products'][1]['parent_asin']
    assert 'previous list' in size_and_detail['assistant']['message']
    size_undo = chat('undo')
    assert 'size' not in size_undo['receipt']['hard']
    assert [p['parent_asin'] for p in size_undo['products']] == [p['parent_asin'] for p in first['products']]
    baseline = runtime.agent.memory.snapshot(sid)
    pending_detail = runtime.agent.memory.pending[sid]
    for message in ('Is it pure cotton?', 'What about the price?', 'Is #2 waterproof?',
                    'Material, please', 'How about its fit?'):
        reply = chat(message)
        assert reply['receipt']['pre_reason'] == 'product_detail'
        assert reply['receipt']['focused_product_id'] == first['products'][1]['parent_asin']
        assert reply['receipt']['new_product_count'] == 0
        assert reply['receipt']['question'] is None
        assert [p['parent_asin'] for p in reply['products']] == [p['parent_asin'] for p in first['products']]
        state = runtime.agent.memory.snapshot(sid)
        assert state.hard_constraints == baseline.hard_constraints
        assert state.soft_preferences == baseline.soft_preferences
        assert state.exclusions == baseline.exclusions
        assert runtime.agent.memory.pending[sid] == pending_detail
        assert not any(e['stage'].startswith('2_retrieval') for e in runtime.agent.get_trace(sid, reply['turn'])['events'])
        if 'waterproof' in message:
            assert "can't reliably answer" in reply['assistant']['message']
        if 'pure cotton' in message:
            assert reply['assistant']['message'].startswith('#2: ')
            assert any(reply['assistant']['message'].startswith(f'#2: {status}')
                       for status in ('Yes.', 'No.', 'I cannot confirm', "I can't confirm"))
        if 'price' in message:
            assert 'does not list a price' in reply['assistant']['message']
    fit_reply = chat('Something looser, please')
    assert fit_reply['receipt']['soft']['style'] == ['loose fit']
    fit_supported = [style_matches(runtime.agent.get_catalog_product(p['parent_asin']), 'loose fit')
                     for p in fit_reply['products']]
    if any(fit_supported):
        assert fit_supported[0]
    fit_undo = chat('undo')
    assert 'style' not in fit_undo['receipt']['soft']
    assert [p['parent_asin'] for p in fit_undo['products']] == [p['parent_asin'] for p in first['products']]
    mixed_reply = chat('Change to blue, and is #2 pure cotton?')
    assert mixed_reply['receipt']['hard']['color'] == 'blue'
    assert mixed_reply['receipt']['compound_request']['detail_context']['parent_asin'] == first['products'][1]['parent_asin']
    assert 'previous list' in mixed_reply['assistant']['message']
    assert mixed_reply['assistant']['message'].count('#2: ') == 1
    assert mixed_reply['products']
    mixed_undo = chat('Undo, and is #2 pure cotton?')
    assert mixed_undo['receipt']['hard']['color'] == 'black'
    assert mixed_undo['receipt']['compound_request']['detail_context']['parent_asin'] == mixed_reply['products'][1]['parent_asin']
    assert [p['parent_asin'] for p in mixed_undo['products']] == [p['parent_asin'] for p in first['products']]
    assert mixed_undo['receipt']['focused_product_id'] == first['products'][1]['parent_asin']
    resumed_detail = chat('Is it pure cotton?')
    assert resumed_detail['receipt']['pre_reason'] == 'product_detail'
    assert resumed_detail['receipt']['focused_product_id'] == first['products'][1]['parent_asin']
    assert resumed_detail['assistant']['message'].startswith('#2: ')
    proposal = chat('Maybe blue')
    assert proposal['receipt']['pre_reason'] == 'confirm_requirement_change'
    assert proposal['receipt']['hard']['color'] == 'black'
    pending = runtime.agent.memory.pending[sid].copy()
    paused = chat('let me think')
    assert runtime.agent.memory.pending[sid] == pending
    assert [p['parent_asin'] for p in paused['products']] == [p['parent_asin'] for p in first['products']]
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, paused['turn'])['events']]
    accepted = chat('replace')
    assert accepted['receipt']['hard']['color'] == 'blue'
    assert accepted['products']
    restored = chat('undo')
    assert restored['receipt']['hard']['color'] == 'black'
    assert restored['products']
    assert [p['parent_asin'] for p in restored['products']] == [p['parent_asin'] for p in first['products']]
    assert restored['receipt']['display_mode'] == 'restored_previous_results'
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, restored['turn'])['events']]
    uncertain = chat("I'm not sure")
    assert uncertain['receipt']['hard'] == restored['receipt']['hard']
    assert uncertain['assistant']['ask_attribute'] is None
    assert uncertain['products']
    assert [p['parent_asin'] for p in uncertain['products']] == [p['parent_asin'] for p in restored['products']]
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, uncertain['turn'])['events']]
    redone = chat('redo')
    assert redone['receipt']['hard']['color'] == 'blue'
    assert redone['receipt']['requirement_redo'] == 'applied'
    assert [p['parent_asin'] for p in redone['products']] == [p['parent_asin'] for p in accepted['products']]
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, redone['turn'])['events']]
    back = chat('undo')
    assert [p['parent_asin'] for p in back['products']] == [p['parent_asin'] for p in first['products']]
    kept_and_refined = chat('Keep #2 and change to blue')
    assert kept_and_refined['receipt']['hard']['color'] == 'blue'
    assert kept_and_refined['selection_state']['selected_asins'] == [back['products'][1]['parent_asin']]
    assert kept_and_refined['receipt']['compound_request']['selection_context'][0]['parent_asin'] == back['products'][1]['parent_asin']
    assert 'Kept #2 from the previous list.' in kept_and_refined['assistant']['message']
    unselected = chat('undo selection')
    assert unselected['selection_state']['selected_asins'] == []
    assert unselected['receipt']['hard']['color'] == 'blue'
    restored_after_keep = chat('undo')
    assert restored_after_keep['receipt']['hard']['color'] == 'black'
    assert [p['parent_asin'] for p in restored_after_keep['products']] == [p['parent_asin'] for p in back['products']]
    mixed_shortlist = chat('Keep #2 and remove #3')
    assert mixed_shortlist['selection_state']['selected_asins'] == [back['products'][1]['parent_asin']]
    assert mixed_shortlist['receipt']['compound_request']['shortlist_actions'] == [
        {'action': 'select', 'ranks': [2]}, {'action': 'remove', 'ranks': [3]},
    ]
    assert [p['parent_asin'] for p in mixed_shortlist['products']] == [p['parent_asin'] for p in back['products']]
    assert not any(e['stage'].startswith('2_retrieval') for e in runtime.agent.get_trace(sid, mixed_shortlist['turn'])['events'])
    cleared_shortlist = chat('undo selection')
    assert cleared_shortlist['selection_state']['selected_asins'] == []
    ranks = ' and '.join(f"#{p['rank']}" for p in uncertain['products'][:2])
    chat(f'Compare {ranks}')
    final = chat('Finalize my selection')
    assert final['selection_state']['status'] == 'finalized'
    selected = final['selection_state']['selected_asins']
    expected_states = (
        ('Remove #2', selected[:1]),
        ('Undo selection', selected),
        ('Redo selection', selected[:1]),
        ('Undo shortlist', selected),
        ("Don't finalize yet", selected),
        ('Do not clear my shortlist', selected),
    )
    for message, expected in expected_states:
        result = chat(message)
        assert result['selection_state']['selected_asins'] == expected
        assert not result['selection_state']['finalized']
        assert result['receipt']['hard'] == back['receipt']['hard']
        assert [p['parent_asin'] for p in result['products']] == [p['parent_asin'] for p in back['products']]
        assert [p['parent_asin'] for p in result['selection_state']['selected_products']] == expected
        assert not any(e['stage'].startswith('2_retrieval') for e in runtime.agent.get_trace(sid, result['turn'])['events'])
    reconfirmed = chat('Please finalize my selection')
    assert reconfirmed['selection_state']['status'] == 'finalized'
    assert reconfirmed['selection_state']['selected_asins'] == selected
    assert runtime.agent.memory.snapshot(sid).soft_preferences['budget_target'][0].value == 30
    return records


def check_feedback_refresh(runtime):
    """Confirm exclusion and fresh retrieval happen in one actual-backend turn."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black cotton tshirt')
    assert len(first['products']) >= 2
    excluded = first['products'][1]['parent_asin']
    reply = runtime.chat(sid, 'Reject #2 and show me more')
    assert not runtime.agent.errors, runtime.agent.errors
    assert reply['turn'] == 2
    assert reply['receipt']['rejected_from_previous_list'] == [2]
    assert excluded in reply['selection_state']['rejected_asins']
    assert excluded not in [product['parent_asin'] for product in reply['products']]
    stages = [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']]
    assert '0_product_feedback' in stages and '2_retrieval' in stages
    restored = runtime.chat(sid, 'undo rejection')
    assert restored['receipt']['rejection_undo'] == 'applied'
    assert excluded not in restored['selection_state']['rejected_asins']
    assert [p['parent_asin'] for p in restored['products']] == [p['parent_asin'] for p in reply['products']]
    assert '2_retrieval' not in [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']]
    redone = runtime.chat(sid, 'redo rejection')
    assert redone['receipt']['rejection_redo'] == 'applied'
    assert excluded in redone['selection_state']['rejected_asins']
    assert [p['parent_asin'] for p in redone['products']] == [p['parent_asin'] for p in reply['products']]
    assert not verify_audit(runtime.audit(sid))
    for result in (reply, restored, redone):
        assert not re.search(r'[\u3400-\u9fff]', result['assistant']['message'])
    return {'turns': 4, 'excluded_parent_asin': excluded, 'products_after_refresh': len(reply['products'])}


def check_similarity_clarification(runtime):
    """Bind a reference rank to catalog facts, then refine without restating it."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a tshirt')
    assert first['products']
    anchor = first['products'][0]['parent_asin']
    question = runtime.chat(sid, 'Show me more like #1')
    assert [p['parent_asin'] for p in question['products']] == [p['parent_asin'] for p in first['products']]
    options = question['receipt']['question']['options']
    assert options and all(option in {'fit', 'color', 'fabric'} for option in options)
    assert question['receipt']['similarity_reference']['parent_asin'] == anchor
    assert '2_retrieval' not in [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']]
    chosen = 'fit' if 'fit' in options else options[0]
    chosen_value = question['receipt']['similarity_reference']['facets'][chosen]['value']
    natural_reply = (f'I like the {chosen_value}' if chosen == 'fit' else
                     f'I like the {chosen_value} {chosen}')
    refined = runtime.chat(sid, natural_reply)
    assert refined['receipt']['similarity_refinement']['parent_asin'] == anchor
    assert refined['receipt']['similarity_refinement']['chosen_facets'] == [chosen]
    assert '2_retrieval' in [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']]
    assert refined['products']
    undone = runtime.chat(sid, 'undo')
    assert [p['parent_asin'] for p in undone['products']] == [p['parent_asin'] for p in first['products']]
    assert undone['receipt']['focused_product_id'] == anchor
    assert '2_retrieval' not in [event['stage'] for event in runtime.agent.get_trace(sid, 4)['events']]
    followup = runtime.chat(sid, 'How much is it?')
    assert followup['receipt']['pre_reason'] == 'product_detail'
    assert followup['receipt']['focused_product_id'] == anchor
    assert followup['assistant']['message'].startswith('#1:')
    assert '2_retrieval' not in [event['stage'] for event in runtime.agent.get_trace(sid, 5)['events']]
    assert not verify_audit(runtime.audit(sid))
    for result in (question, refined, undone, followup):
        assert not re.search(r'[\u3400-\u9fff]', result['assistant']['message'])
    return {'turns': 5, 'anchor_parent_asin': anchor, 'offered_facets': options, 'chosen': chosen}


def check_mixed_similarity_requirement(runtime):
    """A new constraint and an old-list reference must not become a shortlist action."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a tshirt')
    assert first['products']
    anchor = first['products'][0]['parent_asin']
    mixed = runtime.chat(sid, 'Show me more like #1 but in blue')
    assert mixed['receipt']['hard']['color'] == 'blue'
    assert mixed['receipt']['similarity_mixed_request']['parent_asin'] == anchor
    assert not mixed['selection_state']['selected_asins']
    assert '2_retrieval' in [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']]
    recap = runtime.chat(sid, 'What are my preferences?')
    assert recap['receipt']['preference_summary']['hard']['color'] == 'blue'
    assert recap['receipt']['pre_reason'] == 'preference_summary'
    assert '2_retrieval' not in [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']]
    undone = runtime.chat(sid, 'undo')
    assert 'color' not in undone['receipt']['hard']
    assert [product['parent_asin'] for product in undone['products']] == [
        product['parent_asin'] for product in first['products']]
    assert not verify_audit(runtime.audit(sid))
    for result in (first, mixed, recap, undone):
        assert not re.search(r'[\u3400-\u9fff]', result['assistant']['message'])
    return {'turns': 4, 'anchor_parent_asin': anchor,
            'blue_matches': len(mixed['products']), 'undo_restored': True}


def check_preference_comparison(runtime):
    """A personal comparison is a bounded explanation, never a hidden re-search."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a tshirt')
    assert len(first['products']) >= 2
    reply = runtime.chat(sid, 'Which of these is better for me?')
    assert reply['receipt']['pre_reason'] == 'preference_comparison'
    assert len(reply['receipt']['preference_comparison']['rows']) == 2
    assert [item['parent_asin'] for item in reply['products']] == [
        item['parent_asin'] for item in first['products']]
    assert not reply['selection_state']['selected_asins']
    assert '2_retrieval' not in [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']]
    assert not re.search(r'[\u3400-\u9fff]', reply['assistant']['message'])
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 2, 'retained_results': True, 'read_only': True}


def check_scored_question_decline(runtime):
    """Declining an optional catalog question must not save the word 'neither'."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black cotton tshirt around $30')
    assert first['receipt']['question']['options']
    declined = runtime.chat(sid, 'neither')
    assert declined['receipt']['conversation_act'] == 'declined_options'
    assert declined['receipt']['hard'] == first['receipt']['hard']
    assert declined['receipt']['soft'] == first['receipt']['soft']
    assert [item['parent_asin'] for item in declined['products']] == [
        item['parent_asin'] for item in first['products']]
    assert '2_retrieval' not in [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']]
    assert runtime.agent.memory.pending[sid] is None
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 2, 'retained_results': True, 'saved_literal_neither': False}


def check_unmatched_question_reply(runtime):
    """An unrelated short reply cannot become a catalog filter."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black cotton tshirt around $30')
    assert first['receipt']['question']['options']
    reply = runtime.chat(sid, 'banana')
    assert reply['receipt']['conversation_act'] == 'unmatched_option'
    assert reply['receipt']['hard'] == first['receipt']['hard']
    assert reply['receipt']['soft'] == first['receipt']['soft']
    assert [item['parent_asin'] for item in reply['products']] == [
        item['parent_asin'] for item in first['products']]
    assert '2_retrieval' not in [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']]
    assert runtime.agent.memory.pending[sid] is None
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 2, 'retained_results': True, 'saved_literal_banana': False}


def check_unsure_category(runtime):
    """Uncertainty about the product type cannot become a literal category."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'Can you help me shop?')
    assert first['receipt']['question']['target_slot'] == 'category'
    unsure = runtime.chat(sid, 'whatever')
    assert 'category' not in unsure['receipt']['hard']
    assert unsure['products'] == []
    assert '2_retrieval' not in [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']]
    resumed = runtime.chat(sid, 'black cotton tshirt')
    assert resumed['receipt']['hard']['category'] == 't-shirt'
    assert resumed['receipt']['hard']['color'] == 'black'
    assert resumed['receipt']['hard']['material'] == 'cotton'
    assert resumed['products']
    assert not verify_audit(runtime.audit(sid))
    choice_sid = runtime.new_session()['session_id']
    offered = runtime.chat(choice_sid, 'Can you help me shop?')
    assert 't-shirt' in offered['receipt']['question']['options']
    chosen = runtime.chat(choice_sid, 'T-shirt')
    assert chosen['receipt']['hard']['category'] == 't-shirt'
    assert chosen['products']
    assert not verify_audit(runtime.audit(choice_sid))
    return {'turns': 3, 'literal_category_avoided': True,
            'multi_detail_recovered': True, 'suggested_category_selectable': True}


def check_similarity_dislike(runtime):
    """Treat a disliked verified color as a reversible exclusion, not a new question."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a tshirt')
    anchor = first['products'][0]['parent_asin']
    question = runtime.chat(sid, 'Show me more like #1')
    assert 'color' in question['receipt']['question']['options']
    color = question['receipt']['similarity_reference']['facets']['color']['value']
    excluded = runtime.chat(sid, "I don't like the color")
    assert excluded['receipt']['similarity_exclusion']['parent_asin'] == anchor
    assert color in excluded['receipt']['excluded']['color']
    assert anchor not in {product['parent_asin'] for product in excluded['products']}
    lifted = runtime.chat(sid, f'No longer avoid {color}')
    assert color not in lifted['receipt']['excluded'].get('color', [])
    assert color not in lifted['receipt']['soft'].get('color', [])
    assert lifted['receipt']['hard'].get('color') != color
    reexcluded = runtime.chat(sid, 'undo')
    assert color in reexcluded['receipt']['excluded']['color']
    undone = runtime.chat(sid, 'undo')
    assert color not in undone['receipt']['excluded'].get('color', [])
    assert [product['parent_asin'] for product in undone['products']] == [
        product['parent_asin'] for product in first['products']]
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 6, 'excluded_color': color, 'lifted_without_preference': True,
            'undo_restored': True}


def check_preference_withdrawal(runtime):
    """A named removal must not reintroduce the value or erase another requirement."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black cotton tshirt around $30')
    assert first['receipt']['hard']['color'] == 'black'
    assert first['receipt']['hard']['material'] == 'cotton'
    assert first['receipt']['soft']['budget_target'] == ['30.0']
    changed = runtime.chat(sid, 'Remove the black requirement, but keep cotton')
    assert 'color' not in changed['receipt']['hard']
    assert changed['receipt']['hard']['material'] == 'cotton'
    assert 'color' not in changed['receipt']['excluded']
    recap = runtime.chat(sid, 'What are my preferences?')
    assert 'black color' not in recap['assistant']['message']
    assert 'cotton material' in recap['assistant']['message']
    undone = runtime.chat(sid, 'undo')
    assert undone['receipt']['hard']['color'] == 'black'
    assert undone['receipt']['hard']['material'] == 'cotton'
    assert [product['parent_asin'] for product in undone['products']] == [
        product['parent_asin'] for product in first['products']]
    unbudgeted = runtime.chat(sid, 'Remove my $30 budget')
    assert 'budget_target' not in unbudgeted['receipt']['soft']
    assert unbudgeted['receipt']['hard']['color'] == 'black'
    assert unbudgeted['receipt']['hard']['material'] == 'cotton'
    budget_restored = runtime.chat(sid, 'undo')
    assert budget_restored['receipt']['soft']['budget_target'] == ['30.0']
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 6, 'cotton_retained': True, 'black_restored_on_undo': True,
            'budget_restored_on_undo': True}


def check_plain_language_indifference(runtime):
    """Relax one requirement naturally and restore it with undo on real results."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black cotton tshirt')
    assert first['receipt']['hard']['color'] == 'black'
    changed = runtime.chat(sid, "I don't care about color anymore, but keep cotton")
    assert 'color' not in changed['receipt']['hard']
    assert changed['receipt']['hard']['material'] == 'cotton'
    assert changed['products']
    restored = runtime.chat(sid, 'undo')
    assert restored['receipt']['hard']['color'] == 'black'
    assert restored['receipt']['hard']['material'] == 'cotton'
    assert [item['parent_asin'] for item in restored['products']] == [
        item['parent_asin'] for item in first['products']]
    value_changed = runtime.chat(sid, "I don't care about black anymore, but keep cotton")
    assert 'color' not in value_changed['receipt']['hard']
    assert 'color' not in value_changed['receipt']['excluded']
    assert value_changed['receipt']['hard']['material'] == 'cotton'
    value_restored = runtime.chat(sid, 'undo')
    assert value_restored['receipt']['hard']['color'] == 'black'
    assert [item['parent_asin'] for item in value_restored['products']] == [
        item['parent_asin'] for item in first['products']]
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 5, 'color_relaxed': True, 'value_relaxed': True, 'undo_restored': True}


def check_unverifiable_budget(runtime):
    """A real unpriced catalog must not silently satisfy a hard cap."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black tshirt under $50')
    assert not first['products']
    assert first['receipt']['hard']['price_max'] == 50
    assert first['receipt']['question']['reason'] == 'unverifiable_budget'
    assert 'catalog has no prices' in first['assistant']['message']
    preview = runtime.chat(sid, 'Show unpriced ideas')
    assert preview['products']
    assert 'price_max' not in preview['receipt']['hard']
    assert preview['receipt']['soft']['budget_target']
    assert all(runtime.agent.get_catalog_product(product['parent_asin'])['price'] is None
               for product in preview['products'])
    undone = runtime.chat(sid, 'Undo')
    assert undone['receipt']['hard']['price_max'] == 50
    assert not undone['products']
    assert not verify_audit(runtime.audit(sid))
    range_sid = runtime.new_session()['session_id']
    range_first = runtime.chat(range_sid, 'I need a black tshirt over $30 but under $50')
    assert range_first['receipt']['hard']['price_min'] == 30
    assert range_first['receipt']['hard']['price_max'] == 50
    assert not range_first['products']
    assert range_first['receipt']['question']['reason'] == 'unverifiable_budget'
    range_preview = runtime.chat(range_sid, 'Show unpriced ideas')
    assert range_preview['products']
    assert 'price_min' not in range_preview['receipt']['hard']
    assert 'price_max' not in range_preview['receipt']['hard']
    assert range_preview['receipt']['soft']['budget_floor_target']
    assert range_preview['receipt']['soft']['budget_target']
    range_undone = runtime.chat(range_sid, 'Undo')
    assert range_undone['receipt']['hard']['price_min'] == 30
    assert range_undone['receipt']['hard']['price_max'] == 50
    assert not range_undone['products']
    assert not verify_audit(runtime.audit(range_sid))
    return {'turns': 6, 'explicit_preview': True, 'budget_undo_restored': True,
            'floor_and_ceiling_preserved': True}


def check_single_turn_revision(runtime):
    """A correction inside the opening message must replace, not broaden, a slot."""
    sid = runtime.new_session()['session_id']
    result = runtime.chat(sid, 'I need a black cotton tshirt, actually blue')
    assert result['receipt']['hard']['category'] == 't-shirt'
    assert result['receipt']['hard']['color'] == 'blue'
    assert result['receipt']['hard']['material'] == 'cotton'
    assert result['products']
    assert all('blue' in str(runtime.agent.get_catalog_product(p['parent_asin'])).lower()
               for p in result['products'])
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 1, 'old_color_not_saved': True, 'blue_results': len(result['products'])}


def check_correction_acknowledgement(runtime):
    """A correction should name the applied change and retained details."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black cotton tshirt around $30')
    changed = runtime.chat(sid, 'Actually blue, but keep cotton')
    assert changed['receipt']['hard']['color'] == 'blue'
    assert changed['receipt']['hard']['material'] == 'cotton'
    assert changed['receipt']['soft']['budget_target'] == ['30.0']
    assert 'I changed the color from black to blue.' in changed['assistant']['message']
    assert 'I kept cotton' in changed['assistant']['message']
    assert 'roughly $30 budget' in changed['assistant']['message']
    undone = runtime.chat(sid, 'undo')
    assert undone['receipt']['hard'] == first['receipt']['hard']
    assert undone['receipt']['soft'] == first['receipt']['soft']
    revised = runtime.chat(sid, 'Actually blue, size L, around $40')
    assert revised['receipt']['hard']['color'] == 'blue'
    assert revised['receipt']['hard']['size'] == 'l'
    assert revised['receipt']['hard']['material'] == 'cotton'
    assert revised['receipt']['soft']['budget_target'] == ['40.0']
    assert 'color from black to blue' in revised['assistant']['message']
    assert 'size L' in revised['assistant']['message']
    assert 'budget target from around $30 to around $40' in revised['assistant']['message']
    restored = runtime.chat(sid, 'undo')
    assert restored['receipt']['hard'] == first['receipt']['hard']
    assert restored['receipt']['soft'] == first['receipt']['soft']
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 5, 'retained_details_acknowledged': True,
            'multi_detail_acknowledged': True, 'undo_restored': True}


def check_start_over(runtime):
    """Starting fresh clears search state, while a saved shortlist needs scope clarification."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'black cotton tshirt around $30')
    assert first['products']
    reset = runtime.chat(sid, "Let's start over")
    assert reset['receipt']['search_reset']
    assert reset['receipt']['hard'] == {}
    assert reset['receipt']['soft'] == {}
    assert reset['products'] == []
    assert not any(event['stage'].startswith('2_retrieval')
                   for event in runtime.agent.get_trace(sid, reset['turn'])['events'])
    restored = runtime.chat(sid, 'undo')
    assert restored['receipt']['hard'] == first['receipt']['hard']
    assert restored['receipt']['soft'] == first['receipt']['soft']
    assert [p['parent_asin'] for p in restored['products']] == [p['parent_asin'] for p in first['products']]
    runtime.chat(sid, 'Select #1')
    guarded = runtime.chat(sid, 'Start over')
    assert guarded['receipt']['pre_reason'] == 'reset_scope_clarification'
    assert guarded['selection_state']['selected_asins'] == [first['products'][0]['parent_asin']]
    assert guarded['receipt']['hard'] == first['receipt']['hard']
    scoped = runtime.chat(sid, 'Reset search only')
    assert scoped['receipt']['search_reset_scope'] == 'search_only'
    assert scoped['receipt']['hard'] == {}
    assert scoped['selection_state']['selected_asins'] == [first['products'][0]['parent_asin']]
    restored_again = runtime.chat(sid, 'undo')
    assert restored_again['receipt']['hard'] == first['receipt']['hard']
    assert restored_again['selection_state']['selected_asins'] == [first['products'][0]['parent_asin']]
    hidden_id = restored_again['products'][1]['parent_asin']
    rejected = runtime.chat(sid, 'Reject #2')
    assert hidden_id in rejected['receipt']['rejected_asins']
    runtime.chat(sid, 'Start over')
    cleared_all = runtime.chat(sid, 'Reset everything')
    assert cleared_all['receipt']['search_reset_scope'] == 'everything'
    assert cleared_all['receipt']['hard'] == {}
    assert cleared_all['selection_state']['selected_asins'] == []
    assert not cleared_all['receipt']['rejected_asins']
    restored_all = runtime.chat(sid, 'undo')
    assert restored_all['selection_state']['selected_asins'] == [first['products'][0]['parent_asin']]
    assert hidden_id in restored_all['receipt']['rejected_asins']
    redone_all = runtime.chat(sid, 'redo')
    assert redone_all['receipt']['hard'] == {}
    assert redone_all['selection_state']['selected_asins'] == []
    assert not redone_all['receipt']['rejected_asins']
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 12, 'undo_restored': True, 'shortlist_guarded': True,
            'scoped_reset_preserves_shortlist': True, 'full_reset_restores_saved_and_hidden': True}


def check_gift_occasion(runtime):
    """Gift occasion is reversible conversation context, never product proof."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a birthday gift for my sister around $40')
    assert first['receipt']['question']['target_slot'] == 'category'
    assert 'birthday gift for your sister' in first['assistant']['message']
    assert first['receipt']['soft']['shopping_occasion'] == ['birthday']
    second = runtime.chat(sid, 'A black T-shirt')
    assert second['products']
    assert second['receipt']['hard']['category'] == 't-shirt'
    assert second['receipt']['soft']['shopping_occasion'] == ['birthday']
    assert 'birthday gift for your sister' in second['assistant']['message']
    retrieved = next(event['output'] for event in runtime.agent.get_trace(sid, 2)['events']
                     if event['stage'] == '2_retrieval')
    query_terms = str(retrieved['legacy_requirements']['soft_preferences']).lower()
    assert 'birthday' not in query_terms and 'sister' not in query_terms
    changed = runtime.chat(sid, "Actually it's for her graduation")
    assert changed['receipt']['soft']['shopping_occasion'] == ['graduation']
    assert changed['receipt']['soft']['shopping_purpose'] == ['gift for sister']
    undone = runtime.chat(sid, 'Undo')
    assert undone['receipt']['soft']['shopping_occasion'] == ['birthday']
    assert not verify_audit(runtime.audit(sid))
    possessive_sid = runtime.new_session()['session_id']
    natural = runtime.chat(possessive_sid,
                           "I need a black T-shirt for my sister's birthday around $40")
    assert natural['products']
    assert natural['receipt']['soft']['shopping_purpose'] == ['gift for sister']
    assert natural['receipt']['soft']['shopping_occasion'] == ['birthday']
    corrected = runtime.chat(possessive_sid,
                             "Actually, for my brother's graduation")
    assert corrected['receipt']['soft']['shopping_purpose'] == ['gift for brother']
    assert corrected['receipt']['soft']['shopping_occasion'] == ['graduation']
    restored = runtime.chat(possessive_sid, 'Undo')
    assert restored['receipt']['soft']['shopping_purpose'] == ['gift for sister']
    assert restored['receipt']['soft']['shopping_occasion'] == ['birthday']
    assert not verify_audit(runtime.audit(possessive_sid))
    return {'turns': 7, 'occasion_undo_restored': True, 'recipient_retained': True,
            'possessive_occasion_reversible': True}


def check_soft_fit_avoid(runtime):
    """Degree language stays a reversible preference, never a hard filter."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black T-shirt')
    assert first['products']
    guided = runtime.chat(sid, "Nothing too baggy")
    assert guided['products']
    assert guided['receipt']['soft']['fit_avoid'] == ['baggy']
    assert 'fit_avoid' not in guided['receipt']['hard']
    assert 'fit_avoid' not in guided['receipt']['excluded']
    retrieved = next(event['output'] for event in runtime.agent.get_trace(sid, 2)['events']
                     if event['stage'] == '2_retrieval')
    assert 'baggy' not in str(retrieved['legacy_requirements']['soft_preferences'])
    undone = runtime.chat(sid, 'Undo')
    assert 'fit_avoid' not in undone['receipt']['soft']
    assert [p['parent_asin'] for p in undone['products']] == [
        p['parent_asin'] for p in first['products']]
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 3, 'soft_not_filtered': True, 'undo_restored': True}


def check_tentative_size(runtime):
    """An uncertain size must not become a hard or retrieval requirement."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black T-shirt, but I think size M')
    assert first['products']
    assert first['receipt']['soft']['size'] == ['m']
    assert 'size' not in first['receipt']['hard']
    retrieved = next(event['output'] for event in runtime.agent.get_trace(sid, 1)['events']
                     if event['stage'] == '2_retrieval')
    assert 'm' not in retrieved['legacy_requirements']['soft_preferences']
    changed = runtime.chat(sid, 'Actually I wear L, not M')
    assert changed['receipt']['hard']['size'] == 'l'
    undone = runtime.chat(sid, 'Undo')
    assert undone['receipt']['soft']['size'] == ['m']
    assert 'size' not in undone['receipt']['hard']
    assert [p['parent_asin'] for p in undone['products']] == [
        p['parent_asin'] for p in first['products']]
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 3, 'soft_size_not_queried': True, 'undo_restored': True}


def check_optional_question_bypass(runtime):
    """A useful off-question detail should not trigger another optional prompt."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black cotton T-shirt around $30')
    question = first['receipt']['question']
    assert question and question.get('evidence', {}).get('expected_reduction') is not None
    assert question['target_slot'] != 'size'
    supplied = runtime.chat(sid, 'Maybe size M')
    assert supplied['receipt']['soft']['size'] == ['m']
    assert supplied['receipt']['question'] is None
    assert supplied['receipt']['post_reason'] == 'shopper_supplied_other_detail'
    assert supplied['products']
    undone = runtime.chat(sid, 'Undo')
    assert 'size' not in undone['receipt']['soft']
    assert [p['parent_asin'] for p in undone['products']] == [
        p['parent_asin'] for p in first['products']]
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 3, 'question_bypassed': True, 'undo_restored': True}


def check_shopper_focus(runtime):
    """A named decision dimension is not a literal product requirement."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a T-shirt')
    assert first['products']
    focused = runtime.chat(sid, 'Fabric matters more to me')
    assert focused['products']
    assert 'material' not in focused['receipt']['hard']
    assert 'material' not in focused['receipt']['soft']
    question = focused['receipt']['question']
    if question:
        assert question['target_slot'] == 'material'
        chosen = question['options'][0]
        answered = runtime.chat(sid, chosen)
        assert answered['receipt']['soft']['material'] == [chosen]
        undone = runtime.chat(sid, 'Undo')
        assert 'material' not in undone['receipt']['soft']
    else:
        assert focused['receipt']['post_reason'] == 'focus_not_distinguished'
        assert "won't guess" in focused['assistant']['message']
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 4 if question else 2, 'no_literal_fabric': True,
            'grounded_question_or_honest_fallback': True}


def check_comfort_question(runtime):
    """A subjective comparison must not silently edit intent or retrieve again."""
    sid = runtime.new_session()['session_id']
    first = runtime.chat(sid, 'I need a black tshirt')
    assert first['products']
    reply = runtime.chat(sid, 'Which one is most comfortable?')
    assert reply['receipt']['pre_reason'] == 'comfort_question'
    assert reply['receipt']['comfort_question']['verified_winner'] is None
    assert "can't honestly pick" in reply['assistant']['message']
    assert reply['receipt']['hard'] == first['receipt']['hard']
    assert reply['receipt']['soft'] == first['receipt']['soft']
    assert [p['parent_asin'] for p in reply['products']] == [p['parent_asin'] for p in first['products']]
    assert not any(event['stage'].startswith('2_retrieval')
                   for event in runtime.agent.get_trace(sid, reply['turn'])['events'])
    declined = runtime.chat(sid, 'No thanks')
    assert declined['receipt']['pre_reason'] in {'comfort_question_skip', 'offer_declined'}
    assert declined['receipt']['hard'] == first['receipt']['hard']
    assert declined['receipt']['soft'] == first['receipt']['soft']
    assert [p['parent_asin'] for p in declined['products']] == [p['parent_asin'] for p in first['products']]
    assert not any(event['stage'].startswith('2_retrieval')
                   for event in runtime.agent.get_trace(sid, declined['turn'])['events'])
    assert not verify_audit(runtime.audit(sid))
    return {'turns': 3, 'read_only': True, 'decline_read_only': True, 'verified_winner': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', default='I need a blue cotton dress.')
    parser.add_argument('--conversation', action='store_true', help='Also verify multi-turn correction, pause, undo and uncertainty')
    args = parser.parse_args()
    problems = check_search_tool()
    if problems:
        print(json.dumps({'status': 'blocked', 'missing': problems}, indent=2))
        return 2
    before = tool_hashes()
    try:
        runtime = create_runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, args.query)
        if runtime.agent.errors:
            raise RuntimeError(f'Search failed: {runtime.agent.errors[-1]}')
        if not result['products']:
            raise RuntimeError('No eligible products: live selection lifecycle not verified.')
        first_id = result['products'][0]['parent_asin']
        detail = runtime.product_detail(sid, first_id)
        assert detail['parent_asin'] == first_id
        ranks = ' and '.join(f"#{p['rank']}" for p in result['products'][:2])
        runtime.chat(sid, f'Compare {ranks}')
        final = runtime.chat(sid, 'Finalize my selection')
        assert final['selection_state']['status'] == 'finalized'
        assert not verify_audit(runtime.audit(sid))
        report = {'status': 'passed', 'products': len(result['products']),
                  'selected': final['selection_state']['selected_asins'],
                  'backend': 'actual search_tool'}
        if args.conversation:
            report['comfort_question'] = check_comfort_question(runtime)
            report['conversation'] = check_conversation(runtime)
            report['feedback_refresh'] = check_feedback_refresh(runtime)
            report['similarity_clarification'] = check_similarity_clarification(runtime)
            report['mixed_similarity_requirement'] = check_mixed_similarity_requirement(runtime)
            report['similarity_dislike'] = check_similarity_dislike(runtime)
            report['preference_withdrawal'] = check_preference_withdrawal(runtime)
            report['plain_language_indifference'] = check_plain_language_indifference(runtime)
            report['preference_comparison'] = check_preference_comparison(runtime)
            report['scored_question_decline'] = check_scored_question_decline(runtime)
            report['unmatched_question_reply'] = check_unmatched_question_reply(runtime)
            report['unsure_category'] = check_unsure_category(runtime)
            report['unverifiable_budget'] = check_unverifiable_budget(runtime)
            report['single_turn_revision'] = check_single_turn_revision(runtime)
            report['correction_acknowledgement'] = check_correction_acknowledgement(runtime)
            report['start_over'] = check_start_over(runtime)
            report['gift_occasion'] = check_gift_occasion(runtime)
            report['soft_fit_avoid'] = check_soft_fit_avoid(runtime)
            report['tentative_size'] = check_tentative_size(runtime)
            report['optional_question_bypass'] = check_optional_question_bypass(runtime)
            report['shopper_focus'] = check_shopper_focus(runtime)
    finally:
        if before != tool_hashes():
            raise RuntimeError('search_tool file set or content changed during live verification')
    report.update(search_tool_unchanged=True, verified_files=len(before))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
