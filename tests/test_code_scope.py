from textwrap import indent

import pytest

from freya_hackathon.code_scope import isolate_code
from freya_hackathon.offline import execute_python


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
@pytest.mark.parametrize("host_wrapper", [False, True])
def test_helpers_preserve_state_independent_of_host_scope(declaration, host_wrapper):
    code = (
        "count = int(vars['count'])\n"
        "def advance():\n"
        f"    {declaration} count\n"
        "    count += 1\n"
        "advance()\n"
        "vars['count'] = count\n"
    )
    compiled = isolate_code(code)
    assert isolate_code(compiled) == compiled
    if host_wrapper:
        compiled = "def host(vars):\n" + indent(compiled, "    ") + "\nhost(vars)\n"
    result = execute_python(compiled, {"vars": {"count": "0"}})
    assert result["vars"]["count"] == 1


def test_imports_and_mutable_state_are_local_to_the_snippet():
    code = (
        "import json\n"
        "state = {'items': []}\n"
        "def append():\n"
        "    state['items'].append(4)\n"
        "    return json.dumps(state)\n"
        "vars['snapshot'] = append()\n"
    )
    isolated = isolate_code(code)
    wrapped = "def host(vars):\n" + indent(isolated, "    ") + "\nhost(vars)\n"
    result = execute_python(wrapped, {"vars": {}})
    assert result["vars"]["snapshot"] == '{"items": [4]}'


@pytest.mark.parametrize(
    "code",
    [
        "global host_state\nhost_state = 1",
        "def helper():\n    global absent\n    absent = 1\nhelper()",
        "count = 0\ndef outer():\n    count = 4\n    def inner():\n"
        "        global count\n        count = 1\n    inner()\nouter()",
        "def helper():\n    nonlocal missing\n    missing = 1\nhelper()",
    ],
)
def test_ambiguous_or_unbound_state_is_rejected_before_execution(code):
    with pytest.raises(ValueError):
        isolate_code(code)
