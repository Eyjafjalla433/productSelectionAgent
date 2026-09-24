import unittest

from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit
from mvp.control_intent import parse_control_intent
from mvp.product_question import answer_product_question


class ProductQuestionTests(unittest.TestCase):
    def test_short_followups_reference_focus_not_requirements(self):
        for text, attribute in [('Material, please', 'material'), ('What about the price?', 'price'),
                                ('How about its fit?', 'fit'), ('Fabric?', 'material'), ('Cost', 'price')]:
            control = parse_control_intent(text)
            self.assertEqual((control.action, control.ranks, control.attribute, control.uses_focus),
                             ('detail', (), attribute, True))
        for text in ('cotton please', 'price under $30', 'I prefer a loose fit'):
            self.assertIsNone(parse_control_intent(text))

    def test_material_yes_no_questions_do_not_become_requirements(self):
        for message in ('Is the second one cotton?', 'Is #2 pure cotton?',
                        'Is the second one made from 100% cotton?', 'Is #2 a cotton blend?'):
            control = parse_control_intent(message)
            self.assertIsNotNone(control)
            self.assertEqual((control.action, control.ranks, control.attribute), ('detail', (2,), 'material'))
        self.assertTrue(parse_control_intent('Is it polyester?').uses_focus)
        for message in ('I need pure cotton', 'Is #2 cotton? Also change to blue'):
            self.assertIsNone(parse_control_intent(message))

    def test_unsupported_item_questions_are_not_preference_updates(self):
        for message in ('Is #2 waterproof?', 'Does the second one have pockets?',
                        'Is #2 available in size 8?', 'Is #2 cotton or polyester?',
                        'Is #2 cotton and under $30?'):
            control = parse_control_intent(message)
            self.assertEqual((control.action, control.ranks, control.attribute), ('detail', (2,), 'unsupported'))
        self.assertTrue(parse_control_intent('Would it suit me?').uses_focus)
        self.assertIsNone(parse_control_intent('I need waterproof shoes in size 8'))

    def test_english_composition_is_concise_and_source_bound(self):
        product = {'title': 'Comfortable everyday cotton shirt',
                   'features': ['60% cotton; 40% polyester', 'Soft fabric for everyday wear']}
        self.assertEqual(answer_product_question(product, 2, 'material', 'en'),
                         '#2: The listing specifies 60% cotton, 40% polyester.')
        self.assertEqual(answer_product_question({'title': 'Pure cotton tee'}, 1, 'material', 'en'),
                         '#1: The listing describes it as pure cotton.')

    def test_english_summary_does_not_hide_material_uncertainty(self):
        for source in ('Not 100% cotton', 'Shell 100% cotton; lining 100% nylon',
                       '100% cotton; heather colors 60% cotton, 40% polyester',
                       'Approximately 100% cotton', '100% cotton with acrylic'):
            text = answer_product_question({'features': [source]}, 1, 'material', 'en')
            self.assertNotIn('The listing specifies', text)
            self.assertNotIn('describes it as pure cotton', text)
            self.assertIn('check the specific item', text)
        text = answer_product_question({'features': ['60% cotton']}, 1, 'material', 'en')
        self.assertIn('60% cotton', text)
        self.assertNotIn('40%', text)

    def test_fit_questions_are_details_not_requirements(self):
        for message in ('第二款宽松吗？', '第二款的版型怎么样', 'Is the second one fitted?',
                        'What is the fit of the second one?'):
            control = parse_control_intent(message)
            self.assertEqual((control.action, control.ranks, control.attribute), ('detail', (2,), 'fit'))
        self.assertTrue(parse_control_intent('那它修身吗？').uses_focus)
        self.assertIsNone(parse_control_intent('第二款宽松吗？另外我想换成蓝色'))
        self.assertIsNone(parse_control_intent('I need a loose fit tshirt'))

    def test_fit_answers_use_source_without_personal_fit_guarantees(self):
        text = answer_product_question({'title': 'Fitted tee', 'details': {'Fit Type': 'Loose Fit'}}, 2, 'fit', 'zh')
        self.assertIn('宽松版型', text)
        self.assertNotIn('修身版型', text)
        self.assertIn('尺码表', text)
        self.assertIn('不同的版型描述', answer_product_question({'title': 'Loose fit and fitted tee'}, 2, 'fit', 'zh'))
        self.assertIn('没有明确版型', answer_product_question({'title': 'Not fitted tee'}, 2, 'fit', 'zh'))
        self.assertIn('regular fit', answer_product_question({'title': 'Standard-fit tee'}, 2, 'fit', 'en'))

    def test_compact_composition_does_not_combine_conflicting_variants(self):
        source = {'features': ['100% cotton; heather colors 60% cotton, 40% polyester']}
        text = answer_product_question(source, 1, 'material', 'zh')
        self.assertIn('需确认具体款式', text)
        self.assertNotIn('资料标注：棉 100%', text)
        incomplete = answer_product_question({'features': ['60% cotton']}, 1, 'material', 'zh')
        self.assertIn('60% cotton', incomplete)
        self.assertNotIn('40%', incomplete)

    def test_composition_keeps_negation_and_component_scope(self):
        for source in ('Not 100% cotton', 'Not made from 100% cotton',
                       '100% cotton; contains acrylic', 'Approximately 100% cotton',
                       'Shell 100% cotton; lining 100% nylon'):
            text = answer_product_question({'features': [source]}, 1, 'material', 'zh')
            self.assertNotIn('资料标注：', text)
            self.assertNotIn('资料标注为纯棉', text)
        pure = answer_product_question({'features': ['Pure cotton']}, 1, 'material', 'zh')
        self.assertEqual(pure, '第 1 款：资料标注为纯棉。')

    def test_pronouns_are_marked_separately_from_invalid_explicit_ranks(self):
        for text in ('那它多少钱？', '这款是什么材质', 'How much does it cost?', 'What material is that one made of?'):
            control = parse_control_intent(text)
            self.assertEqual(control.action, 'detail')
            self.assertTrue(control.uses_focus)
            self.assertEqual(control.ranks, ())
        self.assertFalse(parse_control_intent('第99款多少钱').uses_focus)

    def test_explicit_questions_are_not_shopping_constraints(self):
        for message, rank, attribute in [('第二款是什么材质？', 2, 'material'),
                                         ('第10款多少钱', 10, 'price'),
                                         ('What material is the second one made of?', 2, 'material'),
                                         ('How much does #1 cost?', 1, 'price')]:
            control = parse_control_intent(message)
            self.assertEqual((control.action, control.ranks, control.attribute), ('detail', (rank,), attribute))
        self.assertIsNone(parse_control_intent('第二款是什么材质，另外换成蓝色'))
        self.assertIsNone(parse_control_intent('I need cotton around $30'))

    def test_material_reply_uses_source_without_certifying_purity(self):
        result = answer_product_question({'features': ['60% cotton; 40% polyester']}, 2, 'material', 'zh')
        self.assertIn('棉 60%', result)
        self.assertIn('聚酯纤维 40%', result)
        self.assertNotIn('纯棉', result)
        self.assertIn('不能确认', answer_product_question({}, 2, 'material', 'zh'))
        self.assertIn('没有给出价格', answer_product_question({}, 2, 'price', 'zh'))

    def test_detail_question_retains_products_requirements_and_pending_question(self):
        calls = []
        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 50-i} for i in range(30)]
        def details(ids):
            return [dict(product_id=i, found=True, title=f"{'Black' if int(i)%2 else 'White'} relaxed-fit cotton tshirt",
                         bullet_point='60% cotton; 40% polyester') for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        state = runtime.agent.memory.snapshot(sid)
        pending = runtime.agent.memory.pending[sid].copy()
        count = len(calls)
        result = runtime.chat(sid, '第二款是什么材质？')
        self.assertIn('60% cotton', result['assistant']['message'])
        self.assertEqual(len(calls), count)
        self.assertEqual(runtime.agent.memory.snapshot(sid).hard_constraints, state.hard_constraints)
        self.assertEqual(runtime.agent.memory.pending[sid], pending)
        self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(result['receipt']['new_product_count'], 0)
        self.assertIsNone(result['receipt']['question'])
        self.assertEqual(result['selection_state']['selected_asins'], [])
        unsupported = runtime.chat(sid, 'Is #2 waterproof?')
        self.assertIn("can't reliably answer", unsupported['assistant']['message'])
        self.assertEqual(runtime.agent.memory.snapshot(sid).hard_constraints, state.hard_constraints)
        self.assertEqual(runtime.agent.memory.snapshot(sid).soft_preferences, state.soft_preferences)
        self.assertEqual(runtime.agent.memory.pending[sid], pending)
        self.assertEqual(len(calls), count)
        self.assertEqual([p['parent_asin'] for p in unsupported['products']], [p['parent_asin'] for p in first['products']])
        short_material = runtime.chat(sid, 'Material, please')
        self.assertEqual(short_material['assistant']['message'], '#2: The listing specifies 60% cotton, 40% polyester.')
        short_price = runtime.chat(sid, 'What about the price?')
        self.assertIn('#2:', short_price['assistant']['message'])
        self.assertIn('does not list a price', short_price['assistant']['message'])
        self.assertEqual(len(calls), count)
        self.assertEqual(runtime.agent.memory.pending[sid], pending)
        material_check = runtime.chat(sid, 'Is it pure cotton?')
        self.assertEqual(material_check['assistant']['message'], '#2: The listing specifies 60% cotton, 40% polyester.')
        self.assertEqual(len(calls), count)
        self.assertEqual(runtime.agent.memory.snapshot(sid).hard_constraints, state.hard_constraints)
        self.assertEqual(runtime.agent.memory.pending[sid], pending)
        followup = runtime.chat(sid, '那它多少钱？')
        self.assertIn('#2', followup['assistant']['message'])
        self.assertIn('does not list a price', followup['assistant']['message'])
        self.assertEqual(followup['receipt']['focused_product_id'], first['products'][1]['parent_asin'])
        self.assertEqual(len(calls), count)
        fit = runtime.chat(sid, '那它宽松吗？')
        self.assertIn('#2: The listing describes a loose fit', fit['assistant']['message'])
        self.assertEqual(fit['receipt']['focused_product_id'], first['products'][1]['parent_asin'])
        self.assertEqual(len(calls), count)
        self.assertEqual(runtime.agent.memory.snapshot(sid).hard_constraints, state.hard_constraints)
        self.assertEqual(runtime.agent.memory.pending[sid], pending)
        missing = runtime.chat(sid, '第99款多少钱')
        self.assertIn('not in the current results', missing['assistant']['message'])
        self.assertEqual(len(calls), count)
        ambiguous = runtime.chat(sid, '它多少钱')
        self.assertIn('Which product', ambiguous['assistant']['message'])
        unknown_fit = runtime.chat(sid, '它宽松吗？')
        self.assertIn('Which product', unknown_fit['assistant']['message'])
        resolved_fit = runtime.chat(sid, '第二款')
        self.assertIn('loose fit', resolved_fit['assistant']['message'])
        runtime.chat(sid, '第99款多少钱')
        resolved = runtime.chat(sid, '第二款')
        self.assertIn('#2', resolved['assistant']['message'])
        self.assertIn('does not list a price', resolved['assistant']['message'])
        runtime.chat(sid, 'show me more')
        self.assertIsNone(runtime.sessions[sid].focused_product_id)
        short_ambiguous = runtime.chat(sid, 'What about its fit?')
        self.assertIn('Which product', short_ambiguous['assistant']['message'])
        short_resolved = runtime.chat(sid, '#1')
        self.assertIn('#1: The listing describes a loose fit', short_resolved['assistant']['message'])
        runtime.chat(sid, '第99款多少钱')
        stale = runtime.chat(sid, 'How much does it cost?')
        self.assertIn('Which product', stale['assistant']['message'])
        runtime.chat(sid, 'black please')
        self.assertEqual(runtime.agent.memory.snapshot(sid).hard_constraints['color'], 'black')
        self.assertFalse(runtime.agent.errors)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
