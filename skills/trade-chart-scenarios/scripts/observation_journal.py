"""Append-only two-stage observation record. No network, orders, or PnL.

The clock is the local system clock, not a trusted timestamp authority. Hashes
bind an outcome to its original decision; they cannot prevent an owner from
replacing the entire directory. Keep the decision hash in an external receipt.
"""
import argparse,hashlib,json,re,os,uuid
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path

def now():return datetime.now(timezone.utc)
def instant(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('timezone_required')
    return parsed.astimezone(timezone.utc)
def stamp(value):return value.isoformat().replace('+00:00','Z')
def number(value):
    if not isinstance(value,str):raise ValueError('decimal_string_required')
    result=Decimal(value)
    if not result.is_finite() or result<=0:raise ValueError('positive_finite_price_required')
    return result
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write_new(path,value):
    encoded=(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        # Windows rename fails if destination exists. POSIX rename replaces it,
        # so POSIX uses a same-directory non-replacing hard-link publication.
        if os.name=='nt':os.rename(temporary,path)
        else:os.link(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()
def load(path):return json.loads(path.read_text(encoding='utf-8'))
def observation(value,clock):
    required={'symbol','feed_id','timeframe','source_sha256','received_at','last_closed_at','close'}
    if not isinstance(value,dict) or not required<=set(value) or set(value)-required-{'open_time'}:
        raise ValueError('unexpected_observation_fields')
    for key in ['symbol','feed_id','timeframe','source_sha256']:
        if not isinstance(value[key],str) or not value[key] or len(value[key])>150:raise ValueError('invalid_identity')
    if not re.fullmatch('[a-f0-9]{64}',value['source_sha256']):raise ValueError('invalid_source_hash')
    if value['timeframe']!='M1':raise ValueError('observation_v1_requires_M1')
    received=instant(value['received_at']);closed=instant(value['last_closed_at'])
    if not closed<=received<=clock or clock-received>timedelta(seconds=120) or received-closed>timedelta(seconds=120):
        raise ValueError('stale_or_future_observation')
    number(value['close'])
    result={**value,'received_at':stamp(received),'last_closed_at':stamp(closed)}
    if 'open_time' in value:
        opened=instant(value['open_time'])
        if closed-opened!=timedelta(minutes=1):raise ValueError('invalid_bar_duration')
        result['open_time']=stamp(opened)
    return result
def record(directory,value,_clock=None):
    clock=_clock or now();obs=observation(value['observation'],clock)
    lower=number(value['lower']);upper=number(value['upper'])
    start=instant(value['target_open']);end=instant(value['target_close'])
    if lower>=upper or not clock<start<end or end-start!=timedelta(minutes=1) or start-clock>timedelta(minutes=2):
        raise ValueError('invalid_fixed_future_condition')
    if start.second or start.microsecond:raise ValueError('unaligned_target_minute')
    payload=dict(schema_version=1,kind='prospective_numeric_observation',recorded_at=stamp(clock),
        observation=obs,lower=str(lower),upper=str(upper),target_open=stamp(start),target_close=stamp(end),
        condition='target closed price strictly above upper / below lower / within inclusive band',
        level_basis='explicit diagnostic band; not a validated Trex level or target',
        analysis_status='observation_only_structural_evidence_incomplete',entry_permission=False,order=None,
        probability=None,position_size=None,profitability_claim=False)
    validate_decision(payload)
    path=Path(directory)/'decision.json';write_new(path,payload)
    return dict(path=str(path),sha256=sha(path),recorded_at=payload['recorded_at'])
def make_outcome(decision,obs,clock,decision_sha256):
    validate_decision(decision)
    obs=observation(obs,clock)
    if any(obs[k]!=decision['observation'][k] for k in ['symbol','feed_id','timeframe']):raise ValueError('feed_identity_mismatch')
    if instant(obs['last_closed_at'])!=instant(decision['target_close']) or instant(obs['open_time'])!=instant(decision['target_open']):
        raise ValueError('wrong_future_bar')
    if not instant(decision['recorded_at'])<instant(decision['target_open'])<instant(obs['last_closed_at'])<=instant(obs['received_at']):
        raise ValueError('not_a_future_observation')
    close=number(obs['close']);lower=number(decision['lower']);upper=number(decision['upper'])
    outcome='above' if close>upper else 'below' if close<lower else 'within'
    payload=dict(schema_version=1,kind='prospective_observation_outcome',recorded_at=stamp(clock),
        decision_sha256=decision_sha256,observation=obs,outcome=outcome,entry_permission=False,order=None,
        fill=None,pnl=None,profitability_claim=False)
    return payload
def settle(directory,value,_clock=None):
    clock=_clock or now();path=Path(directory)/'decision.json';decision=load(path)
    if value['decision_sha256']!=sha(path):raise ValueError('decision_hash_mismatch')
    payload=make_outcome(decision,value['observation'],clock,sha(path))
    out=Path(directory)/'outcome.json';write_new(out,payload)
    return dict(path=str(out),sha256=sha(out),outcome=payload['outcome'])
def validate_decision(value):
    required={'schema_version','kind','recorded_at','observation','lower','upper','target_open','target_close',
        'condition','level_basis','analysis_status','entry_permission','order','probability','position_size','profitability_claim'}
    if set(value)!=required or type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('invalid_decision_schema')
    if value['kind']!='prospective_numeric_observation' or value['analysis_status']!='observation_only_structural_evidence_incomplete':
        raise ValueError('unsupported_decision_kind')
    if value['condition']!='target closed price strictly above upper / below lower / within inclusive band' or value['level_basis']!='explicit diagnostic band; not a validated Trex level or target':
        raise ValueError('unexpected_condition')
    if value['entry_permission'] is not False or value['profitability_claim'] is not False or any(value[k] is not None for k in ['order','probability','position_size']):
        raise ValueError('unexpected_trade')
    clock=instant(value['recorded_at']);observation(value['observation'],clock)
    start=instant(value['target_open']);end=instant(value['target_close'])
    if not clock<start<end or end-start!=timedelta(minutes=1) or start-clock>timedelta(minutes=2) or start.second or start.microsecond:
        raise ValueError('invalid_chronology')
    if number(value['lower'])>=number(value['upper']):raise ValueError('invalid_band')
def verify(directory):
    path=Path(directory)/'decision.json';decision=load(path)
    validate_decision(decision)
    out=Path(directory)/'outcome.json'
    if out.exists():
        value=load(out)
        if value['decision_sha256']!=sha(path):raise ValueError('decision_hash_mismatch')
        if type(value['schema_version']) is not int or value['entry_permission'] is not False or value['profitability_claim'] is not False or any(value[k] is not None for k in ['order','fill','pnl']):
            raise ValueError('unexpected_trade_result')
        clock=instant(value['recorded_at']);expected=make_outcome(decision,value['observation'],clock,sha(path))
        actual={**value,'recorded_at':stamp(clock),'observation':observation(value['observation'],clock)}
        if actual!=expected:raise ValueError('outcome_content_mismatch')
    return dict(status='verified',decision_sha256=sha(path),outcome_sha256=sha(out) if out.exists() else None,
        resolved=out.exists(),external_timestamp_authority=False,trade_performance_verified=False)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['record','settle','verify'])
    p.add_argument('--directory',required=True,type=Path);p.add_argument('--input',type=Path);args=p.parse_args()
    if args.action=='verify':result=verify(args.directory)
    else:
        if not args.input:p.error('--input is required')
        result=(record if args.action=='record' else settle)(args.directory,load(args.input))
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
