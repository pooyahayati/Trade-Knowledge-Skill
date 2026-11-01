"""Bounded downloads of public, real prices; no accounts, keys, or orders.

Only fixed provider hosts and predetermined symbols/periods are used. Raw files
remain outside the skill/publication payload. A failed file is never fabricated.
"""
import concurrent.futures,hashlib,json,time,urllib.request,urllib.parse
from datetime import datetime,timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
SCRATCH=HERE/'local-cache'
RAW=SCRATCH/'stage15-public-data'
MAX_BYTES=10_000_000
HOSTS={'data.binance.vision','query1.finance.yahoo.com'}

def allowed_url(url):
    parts=urllib.parse.urlsplit(url)
    if parts.scheme!='https' or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (None,443):
        raise ValueError('unexpected download URL')

class ProviderRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        # Validate before urllib creates or sends the redirected request.
        allowed_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def stamp():return datetime.now(timezone.utc).isoformat()
def sha(b):return hashlib.sha256(b).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def epoch(s):return int(datetime.fromisoformat(s+'T00:00:00+00:00').timestamp())
def fetch(url):
    allowed_url(url)
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 TradeKnowledgeResearch/0.12'})
    with urllib.request.build_opener(ProviderRedirects()).open(request,timeout=25) as response:
        allowed_url(response.url)
        data=response.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES:raise ValueError('download too large')
        return data,response.headers.get('Content-Type')

def job(item):
    name,url,checksum=item
    path=RAW/name;receipt=RAW/(name+'.receipt.json')
    if path.exists()!=receipt.exists():
        raise ValueError('orphaned raw file or receipt preserved; explicit recovery required')
    if path.exists() and receipt.exists():
        old=json.loads(receipt.read_text())
        if old['file']!=name or old['url']!=url or old['bytes']!=path.stat().st_size or old['sha256']!=sha(path.read_bytes()):
            raise ValueError('cached content or identity changed')
        return old
    error=None
    for attempt in range(2):
        try:
            data,ctype=fetch(url)
            expected=None
            if checksum:
                check,_=fetch(url+'.CHECKSUM')
                expected=check.decode('ascii').split()[0]
                if len(expected)!=64 or expected!=sha(data):raise ValueError('publisher checksum mismatch')
                check_path=RAW/(name+'.CHECKSUM')
                if check_path.exists():
                    if check_path.read_bytes()!=check:raise ValueError('existing publisher checksum changed')
                else:
                    with check_path.open('xb') as check_file:check_file.write(check)
            else:
                doc=json.loads(data)
                if doc.get('chart',{}).get('error') or not doc.get('chart',{}).get('result'):
                    raise ValueError('provider returned no chart')
            with path.open('xb') as raw_file:raw_file.write(data)
            result=dict(file=name,url=url,retrieved_at=stamp(),bytes=len(data),sha256=sha(data),publisher_sha256=expected,content_type=ctype)
            with receipt.open('x',encoding='utf-8') as receipt_file:
                receipt_file.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            return result
        except Exception as exc:
            error=f'{type(exc).__name__}: {str(exc)[:240]}'
            if attempt==0:time.sleep(1)
    return dict(file=name,url=url,error=error,failed_at=stamp())

def main():
    RAW.mkdir(parents=True,exist_ok=True)
    protocol=HERE/'protocol.amended.json'
    if not protocol.exists():
        original=(HERE/'protocol.json').read_bytes();p=json.loads(original)
        p.update(protocol_id='market-transfer-expanded-20261026-v2a',registered_at=stamp(),supersedes_sha256=sha(original))
        p['amendment_reason']='Before any strategy outcome: Dukascopy host connection refused and FXCM weekly archive HTTP403. Use public Yahoo forex quote history within available intraday window. Current month probe was for accessibility only, no strategy outcomes inspected.'
        p['dataset'].update(history_start='2024-11-01T00:00:00Z',history_end='2026-10-01T00:00:00Z',forex_source='Yahoo Finance CCY historical indicative quotes; exact bid/ask basis unverified',history_months=23)
        p['folds']=[dict(id='development',start='2024-11-01T00:00:00Z',end='2025-09-01T00:00:00Z'),dict(id='validation',start='2025-09-01T00:00:00Z',end='2026-01-01T00:00:00Z'),dict(id='holdout',start='2026-01-01T00:00:00Z',end='2026-10-01T00:00:00Z')]
        p['missing_data']='Split every nonconsecutive interval, including weekends; no inferred calendar closures, no interpolation. Reset warmup and censor positions at block/year boundaries. This conservative policy loses opportunities and is reported.'
        p['limits']+=['forex_quote_basis_unverified','forex_last_means_provider_quote_not_exchange_trade','weekend_reset_bias']
        dump(protocol,p)
    jobs=[]
    for symbol in ['BTCUSDT','ETHUSDT']:
        for y in [2024,2025,2026]:
            for m in range(1,13):
                if (y,m)<(2024,11) or (y,m)>(2026,9):continue
                name=f'{symbol}-1h-{y}-{m:02d}.zip'
                jobs.append((name,f'https://data.binance.vision/data/spot/monthly/klines/{symbol}/1h/{name}',True))
    for symbol in ['EURUSD','NZDUSD']:
        for y,start,end in [(2024,'2024-11-01','2025-01-01'),(2025,'2025-01-01','2026-01-01'),(2026,'2026-01-01','2026-10-01')]:
            url=f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}%3DX?interval=1h&period1={epoch(start)}&period2={epoch(end)}'
            jobs.append((f'{symbol}-{y}.json',url,False))
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(job,jobs):
            results.append(result)
            if 'error' in result:print(json.dumps(result),flush=True)
            elif len(results)%10==0:print('retrieved',len(results),'of',len(jobs),flush=True)
    report=dict(protocol_sha256=sha(protocol.read_bytes()),files=results,failures=sum('error' in x for x in results),retrieved_at=stamp())
    dump(HERE/'retrieval.json',report)
    print(json.dumps(dict(files=len(results),failures=report['failures'],total_bytes=sum(x.get('bytes',0) for x in results))),flush=True)
    if report['failures']:raise SystemExit(1)

if __name__=='__main__':main()
