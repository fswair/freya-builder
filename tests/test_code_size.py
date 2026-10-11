import ast

from freya_hackathon.code_size import compact_code
from freya_hackathon.offline import execute_python


def test_compaction_preserves_literals_operators_and_runtime_output():
    code = '''def calculate(values):
    message = """Terms must keep  spaces
        and this indentation.
    """
    # Preserve comments; do not join operators into a different operation.
    values["result"] = (2 ** 3, 9 // 2, 5 - -2, 1 .real, 2 if True else 3)
    values["message"] = message + "héllo  world"
    values["nested"] = [[x + y for y in range(2)] for x in range(2)]
    if True:
        values["formatted"] = f"value  {2 + 3}"
calculate(vars)
'''
    compact = compact_code(code)
    assert len(compact.encode()) < len(code.encode())
    assert ast.dump(ast.parse(compact)) == ast.dump(ast.parse(code))
    assert execute_python(code, {"vars": {}}) == execute_python(compact, {"vars": {}})
    assert compact_code(compact) == compact


def test_compaction_preserves_explicit_continuation_and_adjacent_strings():
    code = (
        "def f(values):\n"
        "    values['x'] = (2 + \\\n"
        "                 3)\n"
        "    values['s'] = 'a  ' ' b'\n"
        "f(vars)\n"
    )
    compact = compact_code(code)
    assert ast.dump(ast.parse(compact)) == ast.dump(ast.parse(code))
    assert execute_python(compact, {"vars": {}})["vars"] == {"x": 5, "s": "a   b"}
