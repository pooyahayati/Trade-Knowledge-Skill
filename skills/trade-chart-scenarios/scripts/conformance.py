"""Offline comparison of submitted indicator exports with frozen golden vectors.

Passing compares data and values, not runtime authenticity or trading usefulness.
Local JSON input/stdout only. Standard library, no network or file writes.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
from decimal import Decimal, localcontext
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONTRACT_BYTES=(ROOT/'references/indicator-conformance-contract.v1.json').read_bytes()
SPEC=json.loads(CONTRACT_BYTES)
CONTRACT_ID=SPEC['contract_id']
_spec=importlib.util.spec_from_file_location('conformance_engine',Path(__file__).with_name('analyze.py'))
A=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)
PRICE=SPEC['price_fields']; RATIO=SPEC['ratio_fields']; EXACT=SPEC['exact_fields']
FIELDS=PRICE+RATIO+EXACT
NUMERIC=re.compile(r'-?[0-9]{1,18}(?:\.[0-9]{1,80})?\Z')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()


def exact_object(value,keys,path):
    if not isinstance(value,dict) or set(value)!=set(keys):A.fail('object_fields_disagree',path)


def schema(value):
    if type(value.get('schema_version')) is not int or value['schema_version']!=1:
        A.fail('unsupported_schema_version','schema_version')
    if value.get('contract_id')!=CONTRACT_ID:A.fail('unsupported_contract','contract_id')


def decimal(value,path):
    if not isinstance(value,str) or not NUMERIC.fullmatch(value):
        A.fail('expected_bounded_decimal_string',path)
    return Decimal(value)


def rows_shape(rows,path):
    if not isinstance(rows,list) or len(rows)>SPEC['limits']['max_rows']:
        A.fail('rows_must_be_bounded_list',path)
    ids=set()
    for i,row in enumerate(rows):
        at=f'{path}[{i}]';exact_object(row,FIELDS,at)
        A.string(row['id'],at+'.id',100)
        if row['id'] in ids:A.fail('duplicate_bar_id',at+'.id')
        ids.add(row['id'])
        for k in PRICE+RATIO:
            if row[k] is not None:decimal(row[k],at+'.'+k)
        for k in ['open_time','close_time','available_at']:A.instant(row[k],at+'.'+k)
        if row['is_closed'] is not True:A.fail('closed_output_required',at+'.is_closed')
        for k in ['standard_dominant_candidate','pinbar_candidate']:
            if row[k] is not None and type(row[k]) is not bool:A.fail('expected_boolean_or_null',at+'.'+k)
        if row['baseline_bar_id'] is not None:A.string(row['baseline_bar_id'],at+'.baseline_bar_id',100)
        if row['length_class'] not in SPEC['length_classes']:A.fail('unknown_length_class',at+'.length_class')


def fixture_bars(fixture):
    exact_object(fixture,SPEC['fixture_keys'],'fixture');schema(fixture)
    A.string(fixture['fixture_id'],'fixture_id',100)
    if not isinstance(fixture['provenance'],dict):A.fail('expected_object','provenance')
    p=fixture['input'];exact_object(p,['schema_version','as_of','series'],'input')
    if type(p['schema_version']) is not int or p['schema_version']!=1:A.fail('unsupported_schema_version','input.schema_version')
    _,period,bars,_=A.parse_series(p['series'],A.instant(p['as_of'],'input.as_of'))
    if period is None:A.fail('explicit_atr_period_required','input.series.atr_period')
    rows_shape(fixture['expected_rows'],'expected_rows')
    if len(fixture['expected_rows'])!=len(bars):A.fail('golden_row_count_disagrees','expected_rows')
    known=None
    for i,(b,r) in enumerate(zip(bars,fixture['expected_rows'])):
        known=max(known,b['available_at']) if known else b['available_at']
        identity=dict(id=b['id'],open_time=A.stamp(b['open_time']),close_time=A.stamp(b['close_time']),available_at=A.stamp(known))
        if any(r[k]!=v for k,v in identity.items()):A.fail('golden_row_identity_disagrees',f'expected_rows[{i}]')
    return period,bars


def reference_export(fixture):
    with localcontext() as ctx:
        ctx.prec=60
        period,bars=fixture_bars(fixture)
        rows=A.calculate(bars,period)
        projected=[]
        for row,bar in zip(rows,bars):
            augmented={**row,'open_time':A.stamp(bar['open_time']),'is_closed':True}
            projected.append({k:augmented[k] for k in FIELDS})
    return dict(schema_version=1,contract_id=CONTRACT_ID,fixture_id=fixture['fixture_id'],fixture_sha256=digest(fixture),
        implementation=dict(platform='python_reference',version=A.VERSION,run_kind='offline_reference'),
        input=copy.deepcopy(fixture['input']),rows=projected)


def compare(fixture,export):
    result=dict(contract_id=CONTRACT_ID,contract_sha256=hashlib.sha256(CONTRACT_BYTES).hexdigest(),
        status='invalid_input',scope='submitted_input_and_values_only',
        native_runtime_execution_verified=False,full_method_validated=False,issues=[])
    try:
        with localcontext() as ctx:
            ctx.prec=100
            fixture_bars(fixture)
            exact_object(export,SPEC['export_keys'],'export');schema(export)
            impl=export['implementation'];exact_object(impl,SPEC['implementation_keys'],'implementation')
            if impl['platform'] not in SPEC['platforms']:A.fail('unsupported_platform','implementation.platform')
            A.string(impl['version'],'implementation.version',100)
            run='offline_reference' if impl['platform']=='python_reference' else 'native_export'
            if impl['run_kind']!=run:A.fail('inconsistent_run_kind','implementation.run_kind')
            result.update(fixture_id=fixture['fixture_id'],platform=impl['platform'],reported_run_kind=run,
                          fixture_sha256=digest(fixture),compared_rows=0,compared_values=0)
            mismatches=[]
            def mismatch(path,reason):
                if len(mismatches)<SPEC['limits']['max_reported_mismatches']:mismatches.append(dict(path=path,reason=reason))
            if export['fixture_id']!=fixture['fixture_id']:mismatch('fixture_id','different_fixture')
            if export['fixture_sha256']!=digest(fixture):mismatch('fixture_sha256','different_fixture_content')
            if digest(export['input'])!=digest(fixture['input']):
                mismatch('input','different_input_history_or_metadata')
            # Different data must not be presented as an algorithm parity result.
            if mismatches:
                result.update(status='mismatch',issues=mismatches);return result
            rows_shape(export['rows'],'rows')
            expected=fixture['expected_rows'];actual=export['rows']
            if len(expected)!=len(actual):mismatch('rows','row_count_disagrees')
            tick=decimal(fixture['input']['series']['tick_size'],'input.series.tick_size')
            for i,(gold,row) in enumerate(zip(expected,actual)):
                for k in FIELDS:
                    a,b=gold[k],row[k];path=f'rows[{i}].{k}'
                    result['compared_values']+=1
                    if k in EXACT or a is None or b is None:
                        if type(a) is not type(b) or a!=b:mismatch(path,'exact_value_disagrees')
                    else:
                        x,y=decimal(a,path),decimal(b,path)
                        absolute=tick*Decimal('.000001') if k in PRICE else Decimal('.000000000001')
                        tolerance=max(absolute,abs(x)*Decimal('.000000000001'))
                        if abs(x-y)>tolerance:mismatch(path,'numeric_tolerance_exceeded')
                result['compared_rows']+=1
            result.update(status='mismatch' if mismatches else 'pass',issues=mismatches)
    except A.InputIssue as exc:
        result['issues']=[dict(code=exc.code,path=exc.path)]
    return result


def load_json(path):
    with Path(path).open('rb') as f:raw=f.read(SPEC['limits']['max_file_bytes']+1)
    if len(raw)>SPEC['limits']['max_file_bytes']:A.fail('input_too_large','file')
    def integer(value):
        if len(value)>18:A.fail('integer_too_large','json')
        return int(value)
    return json.loads(raw.decode('utf-8-sig'),object_pairs_hook=A.no_duplicates,
        parse_float=A.reject_float,parse_constant=A.reject_constant,parse_int=integer)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['reference','compare'])
    parser.add_argument('--fixture',required=True)
    parser.add_argument('--export')
    args=parser.parse_args()
    try:
        fixture=load_json(args.fixture)
        if args.command=='reference':
            if args.export:A.fail('export_not_used_by_reference','arguments')
            result=reference_export(fixture);exit_code=0
        else:
            if not args.export:A.fail('export_required','arguments')
            result=compare(fixture,load_json(args.export))
            exit_code={'pass':0,'mismatch':1,'invalid_input':2}[result['status']]
    except A.InputIssue as exc:
        result=dict(status='invalid_input',issues=[dict(code=exc.code,path=exc.path)]);exit_code=2
    except (OSError,ValueError,RecursionError):
        result=dict(status='invalid_input',issues=[dict(code='unreadable_or_invalid_json',path='file')]);exit_code=2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return exit_code


if __name__=='__main__':sys.exit(main())
