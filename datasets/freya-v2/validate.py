#!/usr/bin/env python3
"""Validate dataset artifacts, never a phone agent or a running tool server.
Default: training and validation only. --include-holdout is for dataset-authoring QA
or a post-selection final check. Reports containing case details stay in that split.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date,datetime,timedelta
from decimal import Decimal
import hashlib,importlib.metadata,importlib.util,itertools,json,re,sys
from pathlib import Path
from urllib.parse import urlparse
from jsonschema import Draft202012Validator,FormatChecker
from validation_support import apply_spec,assert_projection,evaluate
ROOT=Path(__file__).resolve().parent
FMT=FormatChecker()

def strict_load(path):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise ValueError(f'Duplicate JSON key {k} in {path.name}')
            d[k]=v
        return d
    return json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pairs,
                      parse_constant=lambda v:(_ for _ in ()).throw(ValueError('Nonfinite JSON number '+v)))
def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def pointer(value,p):
    if p=='':return value
    assert p.startswith('/'),p
    for token in p[1:].split('/'):
        token=token.replace('~1','/').replace('~0','~')
        value=value[int(token)] if isinstance(value,list) else value[token]
    return value

def exact(a,b):
    if type(a) is not type(b):return False
    if isinstance(a,dict):return a.keys()==b.keys() and all(exact(a[k],b[k]) for k in a)
    if isinstance(a,list):return len(a)==len(b) and all(exact(x,y) for x,y in zip(a,b))
    return a==b

def check_ref(base,ref):
    p=(base/ref['file']).resolve();assert p.is_relative_to(ROOT),ref
    assert p.is_file(),ref
    if 'pointer' in ref:
        pointer(strict_load(p),ref['pointer']);return
    rid=ref['rule_id'];txt=p.read_text()
    if re.fullmatch(r'[AB]\.\d+',rid):
        assert re.search(r'^'+rid.split('.')[1]+r'\.\s',txt,re.M),ref
    elif rid=='B.Closing':assert '## Closing' in txt,ref
    elif p.suffix=='.json':
        def values(x):
            if isinstance(x,dict):return [z for v in x.values() for z in values(v)]
            if isinstance(x,list):return [z for v in x for z in values(v)]
            return [x]
        assert rid in values(strict_load(p)),ref
    else:assert '['+rid+']' in txt,ref

def validate_instance(schema,obj):Draft202012Validator(schema,format_checker=FMT).validate(obj)
def request_schema(api,op):return api['paths']['/'+op]['post']['requestBody']['content']['application/json']['schema']
def response_schema(api,op,status):return api['paths']['/'+op]['post']['responses'][str(status)]['content']['application/json']['schema']
def is_business(d,holidays):return d.weekday()<5 and d not in holidays

def validate_live(splits):
    manifest=strict_load(ROOT/'live/manifest.json');seeds={s['id']:s for s in strict_load(ROOT/'sources/public-test-set.json')}
    api=strict_load(ROOT/'sources/openapi.json');schema=strict_load(ROOT/'schemas/live-case.schema.json')
    src=(ROOT/'sources/brief-a.md').read_text()
    holiday_text=re.search(r'except Freya Bank holidays: ([^\n]+)',src).group(1)
    holidays={date.fromisoformat(x) for x in re.findall(r'\d{4}-\d{2}-\d{2}',holiday_text)}
    bands={key:int(re.search(r'\| '+key+r' \| the (\d+)(?:th|st|nd|rd) business',src).group(1)) for key in ['early','late']}
    def deadline(c,p):
        d=date.fromisoformat(c['variables']['now'][:10]);n=bands['early' if p['days_late']<=7 else 'late']
        while n:
            d+=timedelta(days=1)
            if is_business(d,holidays):n-=1
        return d
    reason_map={'promise':{None},'paid':{None},'transferred':{'restructuring','human','support'},
                'no_promise':{'refused','hardship','dispute','service_error'},'not_verified':{'failed','declined'},
                'not_reached':{'callback','no_callback','wrong_number','deceased','unclear'}}
    for rel,h in manifest['source_sha256'].items():assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()==h
    manifest_ids=[cid for g in manifest['groups'] for cid in g['case_ids']]
    assert len(manifest_ids)==48 and len(set(manifest_ids))==48
    assert len(manifest['groups'])==24 and len({g['group_id'] for g in manifest['groups']})==24
    by_manifest={g['group_id']:g for g in manifest['groups']}
    priority=manifest['priority_training_ids'];assert len(priority)==len(set(priority))==16
    assert sum('-A-' in x for x in priority)==sum('-B-' in x for x in priority)==8
    assert set(priority)<=set(cid for g in manifest['groups'] if g['split']=='train' for cid in g['case_ids'])
    seen=[];groups={};reports={};payloads=0;promise_terms=0;callback_terms=0
    for sp in splits:
        cases=strict_load(ROOT/f'live/{sp}.json');assert len(cases)==(32 if sp=='train' else 8)
        ccount=Counter(c['brief'] for c in cases);assert ccount=={'A':16 if sp=='train' else 4,'B':16 if sp=='train' else 4}
        for c in cases:
            validate_instance(schema,c);seen.append(c);gid=c['metadata']['group_id'];groups.setdefault(gid,[]).append(c)
            assert c['metadata']['split']==sp and c['id'] in by_manifest[gid]['case_ids'] and by_manifest[gid]['split']==sp
            seed=seeds[c['practice_session_case']]
            ignored={'id','family','level','title','script','expected','checks'}
            for k in seed.keys()-ignored:assert k in c and exact(c[k],seed[k]),(c['id'],'fixture mismatch',k)
            assert ('faults' in c)==('faults' in seed)
            for ref in c['metadata']['rule_refs']:check_ref(ROOT,ref)
            assert c['metadata']['oracle_reasoning'].strip() and c['metadata']['specific_risk'].strip()
            exp=c['expected'];assert exp['reason'] in reason_map[exp['outcome']]
            pmap={'p'+str(i+1):p for i,p in enumerate(c['products'])}
            pp=[p['product_id'] for p in exp['promises']];nn=[n['product_id'] for n in exp['notes']]
            assert len(pp)==len(set(pp)) and len(nn)==len(set(nn)) and not set(pp)&set(nn)
            assert set(pp+nn)<=pmap.keys()
            for promise in exp['promises']:
                prod=pmap[promise['product_id']];v=Decimal(str(promise['amount']))
                floor=Decimal(str(prod.get('minimum_payment',prod.get('instalment_amount'))));full=Decimal(str(prod['past_due']))
                assert floor<=v<=full and v==v.quantize(Decimal('0.01'))
                d=date.fromisoformat(promise['date']);assert date.fromisoformat(c['variables']['now'][:10])<=d<=deadline(c,prod) and is_business(d,holidays)
                derived=next(t for t in c['metadata']['derived_terms'] if t['product_id']==promise['product_id'])
                assert derived['deadline']==deadline(c,prod).isoformat() and Decimal(str(derived['floor']))==floor and Decimal(str(derived['full']))==full
                validate_instance(request_schema(api,'create_promise'),{'customer_id':'schema-check-only',**promise});payloads+=1;promise_terms+=1
            for nt in exp['notes']:
                validate_instance(request_schema(api,'add_note'),{'customer_id':'schema-check-only',**nt});payloads+=1
            for cb in exp['callbacks']:
                d=date.fromisoformat(cb['date']);now=datetime.fromisoformat(c['variables']['now']);nextd=now.date()+timedelta(days=1)
                while not is_business(nextd,holidays):nextd+=timedelta(days=1)
                assert d in [now.date(),nextd] and '10:00'<=cb['time']<='19:00' and datetime.fromisoformat(cb['date']+'T'+cb['time'])>now
                validate_instance(request_schema(api,'schedule_callback'),{'customer_id':'schema-check-only',**cb});payloads+=1;callback_terms+=1
            for tr in exp['transfers']:
                validate_instance(request_schema(api,'transfer_to_agent'),{'customer_id':'schema-check-only',**tr});payloads+=1
                assert {'general':'human','support':'support','restructuring':'restructuring'}[tr['queue']]==exp['reason']
            close={'customer_id':'schema-check-only','outcome':exp['outcome']}
            if exp['reason'] is not None:close['reason']=exp['reason']
            validate_instance(request_schema(api,'close_call'),close);payloads+=1
            if c['brief']=='A':assert not exp['notes']
            if exp['transfers']:assert exp['outcome']=='transferred' and not exp['notes'] and len(exp['transfers'])==1
            elif c['brief']=='B' and exp['outcome'] not in ['not_verified','not_reached']:
                assert set(pp+nn)==pmap.keys()
                if pp:assert exp['outcome']=='promise' and exp['reason'] is None
                elif all(n['reason']=='paid' for n in exp['notes']):assert exp['outcome']=='paid'
                else:
                    first=next(n for pid in pmap for n in exp['notes'] if n['product_id']==pid and n['reason']!='paid')
                    assert exp['outcome']=='no_promise' and exp['reason']==first['reason']
            la=c['metadata']['ledger_assertions']
            assert la['successful_promise_count']==len(pp) and la['note_count']==len(nn) and la['close_call_count']==1
        report={'split':sp,'status':'passed','case_count':len(cases),'group_count':len(cases)//2,'errors':[],
                'checks':['strict JSON/schema','registered PUB reference and exact fixture copy including faults','rule/source pointers','allowed ledger payloads and enums','per-product Decimal/date bounds','callback boundaries','closure/transfer/note invariants','pair equality and manifest membership'],
                'limits':['No live session, simulator, builder or three-repetition execution was run.','Natural-language interpretation remains author-reviewed, not empirically verified.']}
        dump(ROOT/f'live/{sp}/validation-report.json',report);reports[sp]=report
    for gid,pair in groups.items():
        assert len(pair)==2
        assert pair[0]['expected']==pair[1]['expected'] and pair[0]['checks']==pair[1]['checks']
        assert pair[0]['script']!=pair[1]['script'] and pair[0]['family']==pair[1]['family']
    assert len({c['id'] for c in seen})==len(seen)
    assert len({c['script'] for c in seen})==len(seen)
    families={}
    for c in seen:families.setdefault(c['family'],set()).add(c['metadata']['split'])
    assert all(len(s)==1 for s in families.values())
    # Remove the repeated simulator safety tail and normal identity scaffolding.
    # Replace numeric variations to avoid treating a changed number as a new family.
    def shingles(c):
        s=c['script'].split(' If a proposed summary')[0]
        marker='do not volunteer either before it is requested.'
        if marker in s:s=s.split(marker,1)[1]
        s=re.sub(r'\b\d+(?:[.,:/-]\d+)*\b','#',s.lower());w=re.findall(r'[a-z]+|#',s)
        return set(zip(w,w[1:],w[2:],w[3:]))
    firsts=[pair[0] for pair in groups.values()];max_cross=0.0;near=[]
    for a,b in itertools.combinations(firsts,2):
        if a['metadata']['split']==b['metadata']['split']:continue
        sa,sb=shingles(a),shingles(b);sim=len(sa&sb)/max(1,len(sa|sb));max_cross=max(max_cross,sim)
        if sim>=0.35:near.append((a['id'],b['id'],sim))
    assert not near,'Cross-split near-duplicate candidate; inspect split-private reports'
    return {'case_count':len(seen),'group_count':len(groups),'priority_training_ids_checked':16,
            'fixture_exact_checks':len(seen),'payload_examples_checked':payloads,'promise_date_amount_checks':promise_terms,
            'callback_date_time_checks':callback_terms,'exact_script_duplicates':0,
            'cross_split_shingle_threshold':0.35,'max_cross_split_shingle_jaccard':round(max_cross,6),
            'near_duplicate_flags':0,'semantic_nonleakage':'Author review plus family grouping; the text heuristic is not a proof of semantic independence.'},[c['id'] for c in seen]

def validate_openapi(api):
    # Deliberately stated as structural plus JSON Schema validation, not a full
    # OpenAPI conformance certification by openapi-spec-validator.
    validate_instance(strict_load(ROOT/'schemas/openapi-minimal.schema.json'),api)
    assert api['openapi'].startswith('3.')
    try:
        from fastapi.openapi.models import OpenAPI
    except ImportError:
        pass
    else:
        OpenAPI.model_validate(api)
    for server in api['servers']:
        u=urlparse(server['url']);assert u.scheme=='https' and u.hostname.endswith('.invalid')
        assert not u.username and not u.password
    ids=[];schemas=0
    for path,item in api['paths'].items():
        assert path.startswith('/') and set(item)=={'post'}
        op=item['post'];assert op['operationId']==path[1:];ids.append(op['operationId'])
        assert op['requestBody']['required'] is True and '200' in op['responses']
        for schema in [op['requestBody']['content']['application/json']['schema']]+[r['content']['application/json']['schema'] for r in op['responses'].values()]:
            Draft202012Validator.check_schema(schema);schemas+=1
        assert op.get('x-policy-refs') and op.get('description')
    assert len(ids)==len(set(ids))
    return schemas

def validate_bundle(path):
    manifest=strict_load(path/'manifest.json');api=strict_load(path/'openapi.json');fixture=strict_load(path/'mock-fixture.json');cases=strict_load(path/'cases.json');oracles=strict_load(path/'oracle.json')
    assert manifest['policy_origin']=='synthetic' and manifest['execution_target']=='local_mock_only'
    assert manifest['tool_contract']=='extended_local_contract'
    assert len(cases)==4 and len({c['id'] for c in cases})==4
    assert set(oracles)==set(fixture['cases'])=={c['id'] for c in cases}
    assert not set(manifest['source_files'])&set(manifest['evaluator_files'])
    for rel in manifest['source_files']+manifest['evaluator_files']:
        assert (path/rel).is_file() and (path/rel).resolve().is_relative_to(path)
    assert fixture['executable_adapter_included'] is False
    assert fixture['case_isolation']['shared_mutable_state'] is False and fixture['case_isolation']['case_order_dependency'] is False
    for r in manifest['policy_rule_index']:check_ref(path,r)
    ns=validate_openapi(api)
    for op,item in api['paths'].items():
        for r in item['post']['x-policy-refs']:check_ref(path,r)
    assert set(fixture['operation_models'])=={x[1:] for x in api['paths']}
    for model in fixture['operation_models'].values():
        for r in model['policy_refs']:check_ref(path,r)
        rid=[r['id'] for r in model['rules']];assert len(rid)==len(set(rid)) and model['rules_are_ordered']
    case_schema=strict_load(ROOT/'schemas/generalization-case.schema.json')
    roles=set();requests=responses=derivations=dup=conflicts=new_key_duplicates=closure_probes=failures=0
    read_events=Counter();reviews=[];sessions=set();all_ops=set()
    for c in cases:
        validate_instance(case_schema,c);cid=c['id'];o=oracles[cid]
        assert c['split']==manifest['split'] and 'practice_session_case' not in c
        roles.update(c['behavior_metadata']['coverage_roles'])
        assert c['fixture_reference']['file']=='mock-fixture.json'
        initial=pointer(fixture,c['fixture_reference']['pointer'])
        assert exact(initial,fixture['cases'][cid]['initial_state'])
        assert initial['closed'] is False and initial['mutation_count']==0 and initial['receipts']=={}
        assert c['variables']['now']==initial['now'] and c['variables']['session_id']==initial['session_id']
        sessions.add(initial['session_id']);s=deepcopy(initial);saved_initial=deepcopy(initial);counts=Counter();used=[]
        for r in o['policy_rule_refs']:check_ref(path,r)
        for d in o['derivation_checks']:
            for r in d['policy_rule_refs']:check_ref(path,r)
            assert evaluate(d['expression'],s=initial)==d['expected'];derivations+=1
        for st in o['feasibility_witness']:
            op=st['operation'];q=st['request'];counts[op]+=1;all_ops.add(op)
            validate_instance(request_schema(api,op),q);requests+=1
            pre=deepcopy(s);s,status,response,rid=apply_spec(fixture,s,op,q)
            validate_instance(response_schema(api,op,status),response);responses+=1
            assert status==st['response_status'] and exact(response,st['response_example']) and rid==st['transition_rule_id']
            failures+=status!=200
            used.append(st)
            # Static metamorphic probes are NOT required agent calls. Run on copies,
            # not on the main witness, to check the declared duplicate semantics.
            if 'request_key' in q and q['request_key'] in s['receipts'] and s['mutation_count']>pre['mutation_count']:
                again,as_,ar,_=apply_spec(fixture,deepcopy(s),op,q)
                validate_instance(response_schema(api,op,as_),ar)
                assert as_==200 and exact(again,s) and again['mutation_count']==s['mutation_count'];dup+=1
                changed=deepcopy(q)
                field=next(k for k,v in changed.items() if k not in ['session_id','request_key','confirmed'] and isinstance(v,str))
                changed[field]='different-static-probe-value'
                # The altered selector is syntactically valid but semantically different.
                # A reused key must not authorize these changed terms.
                validate_instance(request_schema(api,op),changed)
                cs,code,cr,_=apply_spec(fixture,deepcopy(s),op,changed)
                validate_instance(response_schema(api,op,code),cr)
                assert code==409 and cr['error']['code']=='KEY_REUSE' and exact(cs,s);conflicts+=1
                fresh_key=deepcopy(q);fresh_key['request_key'] += ':new-key-duplicate-probe'
                validate_instance(request_schema(api,op),fresh_key)
                ns_,status_,response_,_=apply_spec(fixture,deepcopy(s),op,fresh_key)
                validate_instance(response_schema(api,op,status_),response_)
                assert status_==409 and exact(ns_,s) and response_['ok'] is False
                new_key_duplicates+=1
        assert exact(initial,saved_initial),'Case initial state was mutated by witness evaluation'
        assert_projection(s,o['final_state_projection'])
        assert s['closed'] is True and s['final_code']==o['final_state_projection']['final_code']
        for op,b in o['call_count_bounds'].items():
            assert counts[op]>=b['min'] and (b['max'] is None or counts[op]<=b['max'])
        for a in o['required_actions']:
            matches=[st for st in used if st['operation']==a['operation'] and all(st['request'].get(k)==v for k,v in a.get('payload_contains',{}).items())]
            assert matches,(cid,'Missing required action')
        # The schema enforces enum validity; this verifies that no operation after
        # the terminal close can read or mutate session state.
        for st in used:
            after,status,response,_=apply_spec(fixture,deepcopy(s),st['operation'],st['request'])
            validate_instance(response_schema(api,st['operation'],status),response)
            assert status==409 and response['error']['code']=='CLOSED' and exact(after,s);closure_probes+=1
        # Generic partial-order graph consistency only. Spoken events are not run.
        graph={}
        for rel in o['ordering_constraints']:graph.setdefault(rel['before'],set()).add(rel['after'])
        def visit(node,stack,done):
            assert node not in stack,'Cyclic ordering constraint'
            if node in done:return
            for nxt in graph.get(node,[]):visit(nxt,stack|{node},done)
            done.add(node)
        done=set()
        for n in graph:visit(n,set(),done)
        reviews.append({'case_id':cid,'status':'passed','witness_steps':len(used),'scripted_error_responses':sum(st['response_status']!=200 for st in used),'final_projection_matches':True})
    assert len(sessions)==4
    assert roles>={'ordinary_success','mixed_intent','information_correction_scope','policy_boundary_or_supported_failure'}
    assert all_ops==set(fixture['operation_models']),'Unused API: no case exercises this operation'
    report={'bundle_id':manifest['id'],'split':manifest['split'],'status':'passed','case_count':4,
            'checks':{'schema_nodes_checked':ns,'request_examples':requests,'response_examples':responses,'derivation_checks':derivations,'successful_duplicate_replay_probes':dup,'changed_key_payload_conflict_probes':conflicts,'new_key_duplicate_rejection_probes':new_key_duplicates,'post_close_rejection_probes':closure_probes,'scripted_error_responses':failures,'fresh_initial_states':4},
            'cases':reviews,'limitations':['This is evaluation of authored symbolic witnesses, not a builder, conversation, Freya call or running mock adapter.','Conversation consent, tone and semantic correctness have author review, not measured three-repetition success.','OpenAPI checking is structural plus JSON Schema validation, not full conformance certification.']}
    dump(path/'validation-report.json',report)
    return report,manifest,[c['id'] for c in cases]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-holdout',action='store_true')
    args=parser.parse_args();splits=['train','validation']+(['holdout'] if args.include_holdout else [])
    # Schema validation also checks the validators' schemas are well-formed.
    for schemafile in (ROOT/'schemas').glob('*.json'):Draft202012Validator.check_schema(strict_load(schemafile))
    live,ids=validate_live(splits);gm=strict_load(ROOT/'generalization/manifest.json')
    assert gm['bundle_count']==6 and gm['case_count']==24
    assert len(gm['bundles'])==6 and len({b['id'] for b in gm['bundles']})==6
    for sp in ['train','validation','holdout']:
        bs=[b for b in gm['bundles'] if b['split']==sp];assert len(bs)==2 and Counter(b['sector'] for b in bs)=={'banking':1,'insurance':1}
    total=Counter();families={};raw=0;bundle_count=0
    for b in gm['bundles']:
        if b['split'] not in splits:continue
        rep,m,ci=validate_bundle((ROOT/b['path']).resolve());bundle_count+=1;ids+=ci;total.update(rep['checks'])
        assert m['id']==b['id'] and m['split']==b['split'] and m['sector']==b['sector']
        assert m['brief_family_id'] not in families;families[m['brief_family_id']]=m['split']
        raw+=m['document_format']=='raw_document_bundle'
    assert len(ids)==len(set(ids))
    if args.include_holdout:assert raw>=3 and bundle_count==6
    # Shared artifacts must not contain any copied held-out script/title/rationale.
    # IDs and paths in manifests are allowed. Detailed reports stay in the split.
    leak_scans=0
    if args.include_holdout:
        secret_text=[]
        for c in strict_load(ROOT/'live/holdout.json'):secret_text += [c['script'],c['title'],c['metadata']['oracle_reasoning']]
        for p in (ROOT/'generalization/holdout').glob('*/cases.json'):
            secret_text += [c['script'] for c in strict_load(p)]
            secret_text += [o['rationale'] for o in strict_load(p.parent/'oracle.json').values()]
        for rel in ['live/manifest.json','generalization/manifest.json','README.txt','review.txt']:
            p=ROOT/rel
            if p.exists():
                txt=p.read_text()
                assert not any(s in txt for s in secret_text)
                leak_scans+=1
    authored=[p for p in ROOT.rglob('*.json') if 'schemas' not in p.parts and p.name!='validation-report.json']
    # Do not open held-out content in the default optimizer-facing mode.
    authored=[p for p in authored if args.include_holdout or ('holdout' not in p.parts and p!=ROOT/'live/holdout.json')]
    for p in authored:strict_load(p)
    report={
      'status':'passed','validation_kind':'offline artifact and authored-witness checks; not agent performance',
      'splits_checked':splits,'tooling':{'python':sys.version.split()[0],'jsonschema':importlib.metadata.version('jsonschema'),'fastapi_openapi_model':importlib.metadata.version('fastapi') if importlib.util.find_spec('fastapi') else 'not installed (optional)', 'openapi_spec_validator':'not installed; installation unavailable because the environment could not resolve the package index'},
      'authored_json_documents_parsed':len(authored),'live':live,
      'generalization':{'bundles_checked':bundle_count,'cases_checked':bundle_count*4,'raw_document_bundles_checked':raw,**dict(total)},
      'cross_dataset_unique_case_ids':len(ids),'shared_leak_copy_scans':leak_scans,
      'execution_not_performed':['Freya API/session calls','Builder/GEPA runs','Phone workflow or simulated-customer runs','Running a local mock adapter/server','Three-repetition performance measurement'],
      'qualification':'An authored witness shows static reachability under the supplied declarative specification. It does not prove a generated agent will choose it, understand every script or pass an external judge. Text similarity and copy scans are heuristics, not a semantic secrecy proof.'}
    dump(ROOT/'validation-report.json',report)
    print(json.dumps({'status':'passed','splits':splits,'live_cases':live['case_count'],'local_cases':bundle_count*4,'unique_generated_case_ids':len(ids),'json_parsed':len(authored),'witness_requests':total['request_examples'],'witness_responses':total['response_examples'],'derivations':total['derivation_checks']},indent=2))

if __name__=='__main__':main()
