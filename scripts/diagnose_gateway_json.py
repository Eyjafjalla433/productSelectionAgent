"""Billable diagnostic variants; does not modify production configuration."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agentic_workflow
from shopping_agent.model_provider import create_model_provider
from shopping_agent.requirement_parser import SYSTEM_PROMPT


def run(variant):
    provider=create_model_provider('gateway',timeout_seconds=45)
    original=provider.opener
    record={'variant':variant,'output_limit':512 if variant=='baseline_512' else 1400}
    def opener(req,timeout):
        body=json.loads(req.data)
        if variant=='user_only':
            body['messages']=body['messages'][1:]
        elif variant=='system_only':
            body['messages'][1]['content']=json.dumps({'current_user_message':'I want a cotton blue shirt','rule_hints':[]})
        elif variant=='format_json':
            body['format']='json'
        elif variant=='temperature':
            body['options']['temperature']=0.3
        req.data=json.dumps(body).encode()
        with original(req,timeout=timeout) as response: raw=response.read(1000001)
        record['envelope']=json.loads(raw)
        return io.BytesIO(raw)
    provider.opener=opener
    start=time.perf_counter()
    try:
        r=provider.complete_json(system=SYSTEM_PROMPT,
            user=json.dumps({'current_user_message':'I want a cotton blue shirt','rule_hints':[]}),
            max_tokens=512 if variant=='baseline_512' else 1400)
        record.update(ok=True,data=r.data)
    except Exception as exc:
        record.update(ok=False,error=type(exc).__name__)
    record['elapsed_ms']=round((time.perf_counter()-start)*1000,2)
    Path(f'output/model_comparison_20260928/diagnostic_{variant}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in record.items() if k!='envelope'}),flush=True)
    return record


if __name__=='__main__':
    Path('output/model_comparison_20260928').mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run,['baseline_512','baseline_1400','user_only','system_only','format_json','temperature']))
