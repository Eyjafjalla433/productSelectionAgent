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
          return {passed:true, labels, checks:24};
        })()''')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        connection.close()
        with urlopen(endpoint + '/json/close/' + tab['id']):
            pass


if __name__ == '__main__':
    main()
