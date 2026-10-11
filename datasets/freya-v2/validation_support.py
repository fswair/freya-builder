"""Offline proof-witness evaluator. This is not a tool adapter or an agent runner.

The expression language deliberately has no imports, attributes, comprehensions,
assignment or arbitrary function access. Everything is read from provided JSON.
"""
from __future__ import annotations
import ast
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

def decimal_value(x: Any) -> Decimal:
    return Decimal(str(x))
def round_decimal(x: Any, places: int, rounding: str) -> float:
    import decimal
    allowed = {'ROUND_HALF_UP', 'ROUND_HALF_EVEN', 'ROUND_DOWN', 'ROUND_UP', 'ROUND_FLOOR', 'ROUND_CEILING', 'ROUND_HALF_DOWN', 'ROUND_05UP'}
    if rounding not in allowed or not isinstance(places, int):
        raise ValueError('Unsupported decimal rounding request')
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(str(x)).quantize(quantum, rounding=getattr(decimal, rounding)))
def age_days(now: str, occurred: str) -> int:
    return (date.fromisoformat(now[:10])-date.fromisoformat(occurred)).days
def prev_business_day(iso: str, holidays: list[str]) -> str:
    d=date.fromisoformat(iso)-timedelta(days=1)
    while d.weekday()>4 or d.isoformat() in holidays: d-=timedelta(days=1)
    return d.isoformat()
def cutoff(iso: str, holidays: list[str], local_time: str, offset: str) -> str:
    return prev_business_day(iso,holidays)+'T'+local_time+offset
def count_value(mapping, value):return sum(x==value for x in mapping.values())
def project(mapping, keys):return {k:mapping[k] for k in keys}
def values(mapping):return list(mapping.values())
def concat_lists(*arrays):return sum((list(x) for x in arrays),[])
def add_minutes(iso, minutes):return (datetime.fromisoformat(iso)+timedelta(minutes=minutes)).isoformat()
FUNCTIONS={'len':len,'min':min,'max':max,'str':str,'int':int,'float':float,'abs':abs,
           'decimal_value':decimal_value,'round_decimal':round_decimal,'age_days':age_days,'prev_business_day':prev_business_day,
           'cutoff':cutoff,'count_value':count_value,'project':project,'values':values,
           'concat_lists':concat_lists,'add_minutes':add_minutes}
ALLOWED=(ast.Expression,ast.Constant,ast.Name,ast.Load,ast.Subscript,ast.Dict,ast.List,ast.Tuple,
         ast.BoolOp,ast.And,ast.Or,ast.UnaryOp,ast.Not,ast.USub,ast.UAdd,ast.BinOp,
         ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv,ast.Mod,ast.Compare,ast.Eq,ast.NotEq,
         ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,ast.Is,ast.IsNot,ast.IfExp,ast.Call,ast.Slice)
def evaluate(expr: str, **env):
    tree=ast.parse(expr,mode='eval')
    for node in ast.walk(tree):
        if not isinstance(node,ALLOWED):raise ValueError(f'Unsupported expression node: {type(node).__name__}')
        if isinstance(node,ast.Name) and node.id not in env and node.id not in FUNCTIONS:
            raise ValueError(f'Unknown expression symbol: {node.id}')
        if isinstance(node,ast.Call) and (not isinstance(node.func,ast.Name) or node.func.id not in FUNCTIONS or node.keywords):
            raise ValueError('Only whitelisted positional pure-function calls are allowed')
    return eval(compile(tree,'<declarative-spec>','eval'),{'__builtins__':{}},dict(FUNCTIONS,**env))
def put(state,path,value):
    if not isinstance(path,list) or not path:raise ValueError('Effect path must be a nonempty list')
    obj=state
    for key in path[:-1]:obj=obj[key]
    obj[path[-1]]=deepcopy(value)
def apply_spec(fixture, pre_state, operation, request):
    """Evaluate one author-declared transition over an authored request witness."""
    model=fixture['operation_models'][operation]
    env={'s':deepcopy(pre_state),'q':deepcopy(request),'op':operation}
    for r in model['rules']:
        if evaluate(r['when'],**env):
            response=evaluate(r['response_expression'],**env)
            effects=[(evaluate(e['path_expression'],**env),evaluate(e['value_expression'],**env)) for e in r['effects']]
            new=deepcopy(pre_state)
            for path,val in effects:put(new,path,val)
            return new,r['status'],response,r['id']
    raise AssertionError(f'No declared transition matched {operation}')
def assert_projection(actual,expected,path='$'):
    if isinstance(expected,dict):
        assert isinstance(actual,dict),path
        for k,v in expected.items():
            assert k in actual,(path,k)
            assert_projection(actual[k],v,path+'.'+str(k))
    else:assert actual==expected,(path,actual,expected)
