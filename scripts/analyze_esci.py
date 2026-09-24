"""Download pinned official ESCI files and produce a reproducible descriptive audit.

Install DuckDB separately; does not change the application's dependencies.
python -m pip install --target .local/esci-deps duckdb==1.5.5
python scripts/analyze_esci.py
"""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.local/esci-deps'))
import duckdb

COMMIT = '7916cdf6ab75a462e77f20ab40428a10923998d5'
DATA = ROOT / '.local/esci'
OUT = ROOT / 'docs/research/esci'
FILES = {
    'examples': ('shopping_queries_dataset_examples.parquet', '4a735b693b4a424a6fc67f5be6e4c811495c488bbf66d02a602d308b2744263a'),
    'products': ('shopping_queries_dataset_products.parquet', '25124442d064d64b26f74082d6fa09438d679efc0c183cf28d19064a2b65a265'),
    'sources': ('shopping_queries_dataset_sources.csv', None),
}


def download(item):
    name, (filename, expected) = item
    host = 'media.githubusercontent.com/media' if expected else 'raw.githubusercontent.com'
    url = f'https://{host}/amazon-science/esci-data/{COMMIT}/shopping_queries_dataset/{filename}'
    path = DATA / filename
    if not path.exists():
        print(f'Downloading {name}', flush=True)
        temp = path.with_suffix(path.suffix + '.partial')
        with urllib.request.urlopen(url, timeout=120) as response, temp.open('wb') as f:
            while chunk := response.read(4 * 1024 * 1024):
                f.write(chunk)
        temp.replace(path)
    with path.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    if expected and digest != expected:
        raise ValueError(f'SHA256 mismatch: {path}')
    print(f'Verified {name}: {path.stat().st_size} bytes', flush=True)
    return name, {'url': url, 'sha256': digest, 'bytes': path.stat().st_size}


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        manifest = dict(pool.map(download, FILES.items()))
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute("SET threads=4")
    for name, (filename, _) in FILES.items():
        reader = 'read_csv_auto' if name == 'sources' else 'read_parquet'
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM {reader}('{(DATA / filename).as_posix()}')")
    def rows(sql):
        result = con.execute(sql)
        cols = [x[0] for x in result.description]
        return [dict(zip(cols, r)) for r in result.fetchall()]
    result = {'commit': COMMIT, 'manifest': manifest, 'duckdb_version': duckdb.__version__}
    result['schema'] = {t: rows(f'DESCRIBE {t}') for t in FILES}
    queries = {
        'versions': 'SELECT small_version,large_version,count(*) pairs,count(DISTINCT query_id) queries FROM examples GROUP BY ALL ORDER BY ALL',
        'splits': 'SELECT small_version,product_locale,split,count(*) pairs,count(DISTINCT query_id) queries FROM examples GROUP BY ALL ORDER BY ALL',
        'labels': 'SELECT small_version,product_locale,split,esci_label,count(*) pairs FROM examples GROUP BY ALL ORDER BY ALL',
        'totals': 'SELECT count(*) pairs,count(DISTINCT query_id) query_ids,count(DISTINCT query) query_texts,count(DISTINCT (product_locale,product_id)) product_keys FROM examples',
        'products': 'SELECT product_locale,count(*) products,count(DISTINCT product_id) unique_ids FROM products GROUP BY ALL ORDER BY ALL',
        'duplicate_product_keys': 'SELECT count(*) duplicate_groups FROM (SELECT product_locale,product_id FROM products GROUP BY ALL HAVING count(*)>1)',
        'duplicate_pairs': 'SELECT count(*) duplicate_groups,coalesce(sum(n-1),0) extra_rows FROM (SELECT query_id,product_locale,product_id,count(*) n FROM examples GROUP BY ALL HAVING count(*)>1)',
        'split_overlap_query_id': 'SELECT count(*) queries FROM (SELECT query_id FROM examples GROUP BY ALL HAVING count(DISTINCT split)>1)',
        'split_overlap_query_text': 'SELECT count(*) queries FROM (SELECT lower(trim(query)),product_locale FROM examples GROUP BY ALL HAVING count(DISTINCT split)>1)',
        'missing_products': 'SELECT count(*) pairs FROM examples e ANTI JOIN products p USING(product_locale,product_id)',
        'source_counts': 'SELECT source,count(*) queries FROM sources GROUP BY ALL',
        'missing_sources': 'SELECT count(DISTINCT e.query_id) queries FROM examples e ANTI JOIN sources s USING(query_id)',
        'overlap_details': 'SELECT query_id,first(query) query,list(DISTINCT split ORDER BY split) splits,list(DISTINCT small_version ORDER BY small_version) versions FROM examples GROUP BY query_id HAVING count(DISTINCT split)>1',
        'over_40': 'SELECT small_version,count(*) queries FROM (SELECT small_version,query_id,count(*) n FROM examples GROUP BY ALL) WHERE n>40 GROUP BY ALL ORDER BY ALL',
        'source_slices': "SELECT source,product_locale,split,count(DISTINCT query_id) queries,count(*) pairs FROM examples JOIN sources USING(query_id) WHERE small_version=1 GROUP BY ALL ORDER BY ALL",
        'natural_query_examples': "SELECT DISTINCT e.query_id,e.query,s.source FROM examples e JOIN sources s USING(query_id) WHERE small_version=1 AND product_locale='us' AND split='train' AND source='nlqec' ORDER BY query_id LIMIT 16",
    }
    for name, sql in queries.items():
        print(f'Profiling {name}', flush=True)
        result[name] = rows(sql)
    result['missing_fields'] = []
    for field in ['product_title','product_description','product_bullet_point','product_brand','product_color']:
        result['missing_fields'] += rows(f"SELECT '{field}' field,product_locale,count(*) n,count(*) FILTER(WHERE {field} IS NULL OR trim({field})='') missing,round(avg(length({field})),2) mean_chars FROM products GROUP BY ALL ORDER BY ALL")
    con.execute('CREATE TABLE q AS SELECT small_version,product_locale,split,query_id,first(query) query,count(*) n,count(*) FILTER(WHERE esci_label=\'E\') e,count(*) FILTER(WHERE esci_label=\'S\') s,count(*) FILTER(WHERE esci_label=\'C\') c,count(*) FILTER(WHERE esci_label=\'I\') i FROM examples GROUP BY ALL')
    result['query_groups'] = rows('SELECT small_version,product_locale,split,count(*) queries,min(n) min_candidates,max(n) max_candidates,round(avg(n),2) avg_candidates,median(n) median_candidates,count(*) FILTER(WHERE e=0) no_exact,count(*) FILTER(WHERE e=n) all_exact,count(*) FILTER(WHERE e>0 AND s>0) exact_substitute,count(*) FILTER(WHERE e>0 AND c>0) exact_complement,count(*) FILTER(WHERE e>0 AND s>0 AND c>0 AND i>0) all_four FROM q GROUP BY ALL ORDER BY ALL')
    result['us_train_query_signals'] = rows("SELECT count(*) n,count(*) FILTER(WHERE regexp_matches(query,'(?i)\\b(black|white|red|blue|green|pink|yellow|purple|brown|grey|gray|orange)\\b')) basic_color,count(*) FILTER(WHERE regexp_matches(query,'[0-9]')) has_digits,count(*) FILTER(WHERE regexp_matches(query,'(?i)\\b(for|with|without)\\b')) for_with_without,count(*) FILTER(WHERE len(string_split(trim(query),' '))<=3) short_1_to_3_tokens FROM q WHERE small_version=true AND product_locale='us' AND split='train'")
    # Training-only qualitative inspection: no test examples are exported.
    result['selected_samples'] = rows("SELECT e.query_id,e.query,e.esci_label,e.product_id,p.product_title,p.product_brand,p.product_color,left(p.product_bullet_point,800) bullet_excerpt FROM examples e JOIN products p USING(product_locale,product_id) WHERE e.small_version AND e.product_locale='us' AND e.split='train' AND e.query_id IN (SELECT query_id FROM q WHERE small_version AND product_locale='us' AND split='train' AND e>0 AND s>0 AND c>0 AND i>0 AND regexp_matches(query,'(?i)\\b(black|white|red|blue|iphone|samsung|nike|wireless)\\b') ORDER BY query_id LIMIT 8) QUALIFY row_number() OVER(PARTITION BY e.query_id,e.esci_label ORDER BY e.product_id)<=2 ORDER BY e.query_id,e.esci_label,e.product_id")
    result['natural_product_examples'] = rows("SELECT e.query_id,e.query,e.esci_label,e.product_id,p.product_title,p.product_brand,p.product_color,left(p.product_bullet_point,800) bullet_excerpt FROM examples e JOIN products p USING(product_locale,product_id) WHERE e.small_version AND e.product_locale='us' AND e.split='train' AND e.query_id IN (2162,5611,5624) QUALIFY row_number() OVER(PARTITION BY e.query_id,e.esci_label ORDER BY e.product_id)<=2 ORDER BY e.query_id,e.esci_label,e.product_id")
    (OUT / 'profile.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['schema','selected_samples','manifest','labels']},ensure_ascii=True,indent=2), flush=True)


if __name__ == '__main__':
    main()
