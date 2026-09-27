"""Opt-in billable 5/10-card tests using the paired evaluation fixtures."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import time

from compare_cloud_models import RecordingProvider, create_model_provider, writer_cases, ResponseWriter, writer_checks


def run(mode, cap, repeats, outdir):
    provider=RecordingProvider(create_model_provider(mode))
    if mode=='gateway' and cap is not None:
        original=provider.base.opener
        def opener(request, timeout):
            body=json.loads(request.data)
            body['options']['num_predict']=min(cap,body['options']['num_predict'])
            request.data=json.dumps(body).encode()
            return original(request,timeout=timeout)
        provider.base.opener=opener
    results=[]
    for repeat in range(repeats):
        for count in (5,10):
            args=writer_cases()[0][1]
            products=[]; catalogs={}
            for i in range(count):
                key=f'item{i}'
                product=deepcopy(args['products'][i%2])
                product.update(parent_asin=key,rank=i+1)
                products.append(product)
                catalogs[key]=deepcopy(args['catalogs']['linen' if i%2==0 else 'unknown'])
            args.update(products=products,catalogs=catalogs)
            start=time.perf_counter(); n=len(provider.calls)
            outcome=ResponseWriter(provider).write(**args)
            checks=writer_checks('grounded_en',args,outcome)
            results.append({'repeat':repeat+1,'count':count,'gateway_token_cap':cap if mode=='gateway' else None,
                            'elapsed_ms':round((time.perf_counter()-start)*1000,2),'outcome':outcome,
                            'checks':checks,'output':args,'calls':deepcopy(provider.calls[n:])})
            (outdir/f'stress_{mode}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(mode,repeat+1,count,outcome['status'],checks,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway-token-cap',type=int,help='Optional experiment override; omitted uses production adapter')
    parser.add_argument('--repeats',type=int,default=2,choices=(1,2))
    parser.add_argument('--output',default='output/model_comparison_20260928/candidate_1024')
    args=parser.parse_args(); outdir=Path(args.output); outdir.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,mode,args.gateway_token_cap,args.repeats,outdir) for mode in ('gateway','deepseek')]
        for future in futures: future.result()
