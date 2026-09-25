"""Native-browser reply-control smoke; requires a local CDP browser on 9229.

Uses an owned blank tab and mocked HTTP responses, never a user's existing tab.
Run with python -B -m agentic_workflow.mvp.ui_smoke.
"""
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen


def main():
    import websocket
    root = Path(__file__).parent / 'static'
    endpoint = 'http://127.0.0.1:9229'
    with urlopen(Request(endpoint + '/json/new?about:blank', method='PUT')) as response:
        tab = json.load(response)
    connection = websocket.create_connection(tab['webSocketDebuggerUrl'], suppress_origin=True, timeout=10)
    sequence = 0

    def evaluate(expression):
        nonlocal sequence
        sequence += 1
        connection.send(json.dumps({'id': sequence, 'method': 'Runtime.evaluate',
                                    'params': {'expression': expression, 'awaitPromise': True, 'returnByValue': True}}))
        while True:
            result = json.loads(connection.recv())
            if result.get('id') == sequence:
                break
        assert 'error' not in result, result
        assert 'exceptionDetails' not in result['result'], result
        return result['result']['result'].get('value')

    try:
        html = re.sub(r'<script\b[^>]*>.*?</script>', '', root.joinpath('index.html').read_text(encoding='utf-8'), flags=re.S)
        evaluate('document.open(); document.write(' + json.dumps(html) + '); document.close();')
        evaluate('window.fetch = async () => ({ok:true, json:async()=>({session_id:"ui-test", max_turns:null, scenarios:[], model_provider:"off", search_backend:"search_tool", orchestration_mode:"adaptive"})});')
        evaluate(root.joinpath('app.js').read_text(encoding='utf-8'))
        report = evaluate('''(async () => {
          await new Promise(resolve => setTimeout(resolve, 50));
          const check = (value, message) => { if (!value) throw Error(message); };
          renderReplyOptions({question:{target_slot:'color', options:['black','white']}, can_undo_requirements:true});
          const labels = [...ui.replyOptions.children].map(b => b.textContent);
          check(labels.join('|') === 'black|white|Show me first|Undo', 'grounded choices');
          renderReplyOptions({hard:{category:'t-shirt'}});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'Start over', 'reset visible for an active search');
          renderReplyOptions({question:{target_slot:'category', options:['t-shirt','dress'],
            option_labels:{'t-shirt':'T-shirt',dress:'Dress'}}});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'T-shirt|Dress', 'category labels');
          renderReplyOptions({question:{target_slot:'reset_scope', options:['Reset search only','Reset everything','Cancel']}});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'Reset search only|Reset everything|Cancel', 'reset scope choices');
          renderReplyOptions({suggested_replies:['T-shirt','Dress']});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'T-shirt|Dress', 'reoffered starting points');
          renderReplyOptions({question:{target_slot:'color',options:['black','white']},
            comparison_reference_question:{displayed_ranks:[1,2,3,4],pending_requirements:'I prefer cotton'},
            suggested_replies:['Never mind']});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'Never mind',
            'comparison reference clarification offers dismissal instead of stale refinement');
          renderReplyOptions({question:{target_slot:'color',options:['black','white']},
            detail_question:{attribute:'care',ranks:[1,2,3]}, suggested_replies:['#1','#2','#3','Never mind']});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === '#1|#2|#3|Never mind',
            'product reference choices take priority over older search question');
          renderReplyOptions({question:{target_slot:'color', options:['black','white']},
            preference_comparison:{question:{slot:'style', options:[{value:'slim fit'},{value:'regular fit'}]}},
            suggested_replies:['slim fit','regular fit','Either is fine']});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'slim fit|regular fit|Either is fine',
            'active comparison takes priority over earlier refinement');
          renderReplyOptions({suggested_replies:['black','blue','Either is fine'], can_undo_requirements:true});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'black|blue|Either is fine|Undo',
            'comparison choices retain undo');
          renderReplyOptions({question:{target_slot:'color', options:['black','white']}, can_undo_requirements:true});
          ui.message.value = 'my draft'; ui.message.dispatchEvent(new Event('input'));
          check([...ui.replyOptions.children].every(b=>b.disabled), 'protect draft');
          ui.replyOptions.firstChild.click(); check(ui.message.value === 'my draft', 'draft unchanged');
          ui.message.value = ''; ui.message.dispatchEvent(new Event('input'));
          let calls = 0; let sent = ''; let release;
          window.fetch = async (path, options) => {
            calls++; sent = JSON.parse(options.body).message;
            await new Promise(resolve => { release = resolve; });
            return {ok:false, status:503, json:async()=>({error:'simulated retry', error_code:'temporary'})};
          };
          ui.replyOptions.firstChild.click(); ui.composer.requestSubmit();
          check(calls === 1 && sent === 'black', 'one click one request');
          check(ui.submit.disabled && ui.newSession.disabled, 'in-flight controls');
          release(); await new Promise(resolve => setTimeout(resolve, 20));
          check(!ui.submit.disabled && !ui.newSession.disabled, 'retry controls restored');
          renderReplyOptions({question:{correction:{}, target_slot:'color'}});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'Replace|Keep both|Keep original|Show me first', 'correction choices');
          renderReplyOptions({}); check(ui.replyOptions.hidden && !ui.replyOptions.children.length, 'stale options removed');
          renderReplyOptions({can_redo_requirements:true});
          check(ui.replyOptions.children.length === 1 && ui.replyOptions.firstChild.textContent === 'Redo', 'redo availability');
          renderReplyOptions({can_undo_rejection:true, can_redo_rejection:true});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'Undo rejection|Redo rejection', 'product feedback corrections');
          renderReplyOptions({question:{target_slot:'similarity_attribute', options:['fit','color','fabric']}});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'fit|color|fabric|Show me first', 'grounded similarity choices');
          renderReplyOptions({question:{target_slot:'comfort', options:['breathability','relaxed fit']}});
          check([...ui.replyOptions.children].map(b=>b.textContent).join('|') === 'breathability|relaxed fit|Show me first',
            'comfort choices and a browsing escape remain available');
          renderReplyOptions({question:{target_slot:'material', options:['fleece'], option_labels:{fleece:'抓绒'}}});
          check(ui.replyOptions.firstChild.textContent === 'fleece', 'English options regardless of legacy labels');
          shortlisted.clear();
          currentSelectionState = {can_undo_selection:true, finalized:false};
          renderShortlist();
          check(!ui.shortlist.hidden && !ui.undoShortlist.hidden, 'undo remains visible after clear');
          ui.message.value = 'keep my draft';
          ui.undoShortlist.click();
          check(ui.message.value === 'keep my draft', 'shortlist undo protects draft');
          ui.message.value = '';
          const beforeUndo = calls;
          ui.undoShortlist.click();
          check(ui.message.value === 'Undo selection' && calls === beforeUndo, 'shortlist undo loads without sending');
          currentSelectionState = {can_undo_selection:false, finalized:false};
          renderShortlist();
          check(ui.shortlist.hidden && ui.undoShortlist.hidden, 'exhausted undo hidden');
          syncSelection({selected_asins:['old-2','old-1'], selected_products:[
            {parent_asin:'old-1',title:'Earlier cotton shirt',price:null,rating:null},
            {parent_asin:'old-2',title:'Earlier linen shirt',price:null,rating:null}
          ],can_undo_selection:true}, []);
          check([...shortlisted.keys()].join('|') === 'old-2|old-1', 'restored backend order');
          check(ui.shortlistItems.textContent.includes('Earlier cotton shirt') && ui.shortlistItems.textContent.includes('PRICE N/A'), 'restored cached details');
          check(ui.shortlistCount.textContent === '2 / 3', 'restored count matches backend');
          syncSelection({selected_asins:[],can_redo_selection:true}, []);
          check(!ui.shortlist.hidden && !ui.redoShortlist.hidden && ui.undoShortlist.hidden, 'redo available for empty shortlist');
          ui.message.value = 'unfinished draft'; ui.redoShortlist.click();
          check(ui.message.value === 'unfinished draft', 'redo protects draft');
          ui.message.value = ''; ui.redoShortlist.click();
          check(ui.message.value === 'Redo selection' && calls === beforeUndo, 'redo loads without sending');
          let finishClear;
          window.fetch = async () => {
            await new Promise(resolve => { finishClear = resolve; });
            return {ok:true,json:async()=>({selected_asins:[],can_undo_selection:true})};
          };
          ui.clearShortlist.disabled = false;
          ui.clearShortlist.click();
          sessionId = 'new-session';
          syncSelection({selected_asins:['new'],selected_products:[{parent_asin:'new',title:'New session choice'}]}, []);
          finishClear(); await new Promise(resolve=>setTimeout(resolve,20));
          check(shortlisted.has('new') && shortlisted.size === 1, 'late clear cannot overwrite new shortlist');
          const chatCount = ui.chat.children.length;
          let failExport;
          window.fetch = async () => {
            await new Promise(resolve => { failExport = resolve; });
            return {ok:false,status:503,json:async()=>({error:'old export failed'})};
          };
          ui.exportSelection.disabled = false; ui.exportSelection.click();
          sessionId = 'another-session';
          failExport(); await new Promise(resolve=>setTimeout(resolve,20));
          check(ui.chat.children.length === chatCount, 'late error cannot pollute new conversation');
          shortlisted.set('new',{parent_asin:'new',title:'Saved shirt',comparisonDraft:{pros:[{text:'Old recommendation'}]}});
          syncSelection({selected_asins:['new'],selected_products:[{
            parent_asin:'new',title:'Saved shirt',match:{signals:[{tier:'hard',slot:'color',status:'not_evidenced'}]},
            advice:{pros:[],cons:[]}
          }]}, []);
          check(!shortlisted.get('new').comparisonDraft, 'stale model comparison removed');
          check(ui.shortlistItems.textContent.includes('Not all current requirements'), 'saved choice recheck warning');
          const comparisonProducts = [1,2,3].map(rank => ({rank, parent_asin:`demo-${rank}`,
            title:`Shirt ${rank}`, category:'t-shirt', store:'Demo', price:null,
            shopper_notes:{feature:'Cotton', detail:`Listed size ${['M','S','L'][rank-1]}`},
            evidence:[], match:{hard_supported:0,hard_total:0,soft_supported:0,soft_total:0,signals:[]},
            advice:{pros:[],cons:[]}}));
          renderProducts(comparisonProducts, false, {comparison_takeaway:'The listing titles show different sizes: #1 M, #2 S, #3 L.'});
          check(ui.products.querySelector('.shopping-top-three p').textContent.includes('#1 M, #2 S, #3 L'),
            'size contrast appears in the comparison panel');
          const descriptionHandoff = {selected_products:[
            {parent_asin:'A',title:'Cotton dress'}, {parent_asin:'B',title:'Second dress'}],
            comparison_assist:{schema_version:'description-comparison.v1',status:'completed',
              objective_comparison:{comparison_matrix:[
                {dimension:'brand',values:{A:{value:'Example',source_type:'explicit',evidence:'Example'},B:{value:'Example',source_type:'explicit',evidence:'Example'}}},
                {dimension:'material',values:{A:{value:'Cotton',source_type:'inferred',evidence:'Cotton blend'},B:{value:null}}},
                {dimension:'warranty',values:{A:{value:null},B:{value:null}}}],
                product_assessments:[{parent_asin:'A',pros:[{text:'Soft fabric <img src=x>',evidence_refs:[{quote:'Cotton blend'}]}]}],
                trade_offs:[]},
              personalized_comparison:{personalization_applied:true,products:[
                {parent_asin:'A',fit_reasons:[{text:'An option for your cotton preference.',evidence_refs:[{quote:'Cotton blend'}]}]}]}}};
          renderComparison(descriptionHandoff);
          check(!ui.comparison.hidden && ui.comparisonTable.querySelector('thead').textContent.includes('Cotton dressSecond dress'), 'description selection order');
          check(ui.comparisonTable.textContent.includes('Unknown') && ui.comparisonTable.textContent.includes('Inference'), 'unknown and inferred values labeled');
          check(ui.comparisonTable.textContent.includes('Details not supplied (1)'), 'missing dimensions grouped');
          check(ui.comparisonDetails.querySelector('.personalized-comparison').textContent.includes('cotton preference'), 'personal advice separate from facts');
          check(ui.comparisonDetails.textContent.includes('<img src=x>') && !ui.comparisonDetails.querySelector('img'), 'model prose rendered as text');
          check(!ui.comparisonTable.querySelector(':scope > table').textContent.includes('Brand') && ui.comparisonTable.textContent.includes('Shared details (1)'), 'shared facts are expandable');
          check(ui.comparisonTable.querySelector(':scope > table').textContent.includes('Unknown'), 'partly missing details remain visible');
          const sameValue = structuredClone(descriptionHandoff);
          sameValue.comparison_assist.objective_comparison.comparison_matrix[1].values.B = {value:'Cotton',source_type:'explicit',evidence:'Cotton'};
          renderComparison(sameValue);
          check(ui.comparisonTable.querySelector(':scope > table').textContent.includes('Inference'), 'same value with inferred evidence remains visible');
          sameValue.comparison_assist.objective_comparison.comparison_matrix[1].values.A.source_type = 'explicit';
          renderComparison(sameValue);
          check(ui.comparisonTable.querySelector(':scope > table thead').textContent.includes('Cotton dressSecond dress') && ui.comparisonTable.querySelector(':scope > table tbody').textContent.includes('available listed values are the same'), 'all-shared comparison keeps names visible without implying a winner');
          sameValue.selected_products = [sameValue.selected_products[0]];
          renderComparison(sameValue);
          check(ui.comparisonTable.querySelector(':scope > table').textContent.includes('Brand') && !ui.comparisonTable.textContent.includes('Shared details'), 'single item keeps facts visible');
          const prioritized = structuredClone(descriptionHandoff);
          prioritized.requirements = {hard:{price_max:30},soft:{material:['cotton']}};
          prioritized.comparison_assist.objective_comparison.comparison_matrix[1].values = {
            A:{value:'Cotton',source_type:'explicit'},B:{value:'Cotton',source_type:'explicit'}};
          prioritized.comparison_assist.objective_comparison.comparison_matrix.push({dimension:'price',values:{A:{value:null},B:{value:null}}});
          const beforePriorityRender = JSON.stringify(prioritized);
          renderComparison(prioritized);
          const priorityRows = ui.comparisonTable.querySelectorAll(':scope > table tbody tr');
          check(priorityRows[0].textContent.includes('PriceYour requirement') && priorityRows[0].textContent.includes('Unknown'), 'requested unknown price stays first and visible');
          check(priorityRows[1].textContent.includes('MaterialYour preferenceCottonCotton'), 'requested shared fabric stays visible');
          check(JSON.stringify(prioritized) === beforePriorityRender, 'presentation does not mutate comparison evidence');
          descriptionHandoff.comparison_scope = 'requested_products';
          descriptionHandoff.saved_asins = ['A'];
          renderComparison(descriptionHandoff);
          check(ui.comparisonNote.textContent.includes('Some compared items are not saved'), 'comparison scope distinct from shortlist');
          descriptionHandoff.comparison_assist.status = 'partial';
          renderComparison(descriptionHandoff);
          check(ui.comparisonNote.textContent.includes('could not be completed'), 'partial comparison explained');
          renderComparison(null);
          check(ui.comparison.hidden && !ui.comparisonDetails.children.length, 'old description cleared');
          ui.message.value = 'an unfinished preference';
          ui.message.dispatchEvent(new Event('input'));
          check(ui.compareSelection.disabled, 'saved comparison protects draft');
          ui.compareSelection.click();
          check(ui.message.value === 'an unfinished preference', 'comparison does not replace draft');
          ui.message.value = ''; ui.message.dispatchEvent(new Event('input'));
          let comparisonCalls = 0; let comparisonMessage; let completeComparison;
          window.fetch = async (path, options) => {
            comparisonCalls++; comparisonMessage = JSON.parse(options.body).message;
            await new Promise(resolve => { completeComparison = resolve; });
            return {ok:false,status:503,json:async()=>({error:'simulated comparison retry'})};
          };
          ui.compareSelection.click(); ui.compareSelection.click();
          check(comparisonCalls === 1 && comparisonMessage === 'Compare my saved options', 'one click compares saved IDs without displayed ranks');
          check(ui.compareSelection.disabled, 'comparison disabled in flight');
          completeComparison(); await new Promise(resolve => setTimeout(resolve,20));
          check(!ui.compareSelection.disabled, 'comparison can retry');
          shortlisted.clear(); renderShortlist();
          check(ui.compareSelection.disabled, 'no comparison for an empty shortlist');
          return {passed:true, labels, checks:58};
        })()''')
        from agentic_workflow.showcase import render, run_case
        replay = render(run_case('demo', 'flexible'))
        script = re.findall(r'<script\b[^>]*>(.*?)</script>', replay, flags=re.S)[-1]
        evaluate('document.open(); document.write(' + json.dumps(replay) + '); document.close();')
        evaluate('(function(){' + script + '''
          if (steps.length !== 7 || tabs.length !== 7) throw Error('Seven replay steps required');
          tabs[3].click();
          if (document.querySelector('#progress').textContent.includes('Shortlist confirmed'))
            throw Error('Preference reply must not appear finalized');
          tabs[6].click();
          if (!document.querySelector('#progress').textContent.includes('Shortlist confirmed') || !next.disabled)
            throw Error('Final replay step must confirm the saved shortlist');
        })()''')
        report['checks'] += 3
        revision_replay = render(run_case('demo', 'revisions'))
        revision_script = re.findall(r'<script\b[^>]*>(.*?)</script>', revision_replay, flags=re.S)[-1]
        evaluate('document.open(); document.write(' + json.dumps(revision_replay) + '); document.close();')
        evaluate('(function(){' + revision_script + '''
          if (steps.length !== 6 || tabs.length !== 6) throw Error('Six revision steps required');
          tabs[2].click();
          if (document.querySelector('#progress').textContent.includes('Shortlist confirmed'))
            throw Error('Undo must not appear finalized');
          tabs[5].click();
          if (!document.querySelector('#progress').textContent.includes('Shortlist confirmed') || !next.disabled)
            throw Error('Final revision step must confirm the saved shortlist');
        })()''')
        report['checks'] += 3
        clarification_replay = render(run_case('demo', 'clarification'))
        clarification_script = re.findall(r'<script\b[^>]*>(.*?)</script>', clarification_replay, flags=re.S)[-1]
        evaluate('document.open(); document.write(' + json.dumps(clarification_replay) + '); document.close();')
        evaluate('(function(){' + clarification_script + '''
          if (steps.length !== 10 || tabs.length !== 10) throw Error('Ten clarification steps required');
          tabs[6].click();
          if (document.querySelector('#progress').textContent.includes('Shortlist confirmed'))
            throw Error('Comparison must not appear finalized');
          tabs[9].click();
          if (!document.querySelector('#progress').textContent.includes('Shortlist confirmed') || !next.disabled)
            throw Error('Final clarification step must confirm the saved shortlist');
        })()''')
        report['checks'] += 3
        print(json.dumps(report, ensure_ascii=False))
    finally:
        connection.close()
        with urlopen(endpoint + '/json/close/' + tab['id']):
            pass


if __name__ == '__main__':
    main()
