"""Create a new reference version with bounded controller failure paths."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json

RECOVERY = """import json
def object_value(value):
 try:
  value = json.loads(value) if isinstance(value, str) else value
  return value if isinstance(value, dict) else dict()
 except Exception:
  return dict()
s = object_value(vars.get(STATE_KEY, ''))
data = object_value(vars.get('api_data', ''))
pending = s.get('pending', '') if STATE_KEY == 'call_state' else vars.get('event', '')
ok = str(data.get('ok', False)).lower() == 'true'
products = s.get('products', [])
products = list(products.values()) if isinstance(products, dict) else products
products = [p for p in products if isinstance(p, dict)] if isinstance(products, list) else []
recorded = any(p.get('recorded') is True or p.get('status') == 'recorded' for p in products)
recorded = recorded or (pending in ('create', 'create_promise') and ok)
transferred = s.get('transfer_success') is True or s.get('outcome') == 'transferred'
transferred = transferred or (pending in ('transfer', 'transfer_to_agent') and ok)
outcome = 'no_promise' if s.get('verified') is True else 'not_reached'
reason = 'service_error' if outcome == 'no_promise' else 'unclear'
message = ('I am sorry, I cannot continue this call. '
           'Please contact Freya Bank for further assistance.')
if not s.get('verified') and s.get('stage', s.get('phase')) == 'security':
 outcome, reason = 'not_verified', 'declined'
if products and all(p.get('reason') == 'paid' for p in products):
 outcome, reason = 'paid', ''
if pending in ('callback', 'schedule_callback') and ok:
 outcome, reason = 'not_reached', 'callback'
 message = 'Your callback is scheduled. I am sorry, I cannot continue this call.'
if recorded:
 outcome, reason = 'promise', ''
 message = ('Your recorded promise remains in place. '
            'You will receive a text message confirming this promise. '
            'I am sorry, I cannot continue this call.')
if transferred:
 queue = (s.get('transfer_reason') or s.get('requested_transfer_reason')
          or s.get('queue') or s.get('reason'))
 reason = 'human' if queue == 'general' else queue
 if reason in ('human', 'support', 'restructuring'):
  outcome = 'transferred'
  message = 'Your transfer request was accepted. I am sorry, I cannot continue this call.'
 else:
  reason = ('' if outcome in ('promise', 'paid')
            else 'service_error' if outcome == 'no_promise' else 'unclear')
vars['outcome'], vars['reason'] = outcome, reason
vars[FAREWELL_KEY] = message + ' Thank you. Goodbye.'
vars['next'], vars['route'] = 'goodbye', 'farewell'
vars['event'], vars['api_data'] = '', '{}'
s['pending'], s['retry_kind'] = '', ''
s['terminal'], s['terminal_selected'] = True, True
vars[STATE_KEY] = json.dumps(s)
"""


def code_default(node: dict) -> dict:
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]


def compact(code: str) -> str:
    """Shrink indentation only after normalizing, with exact AST equivalence."""
    normalized = ast.unparse(ast.parse(code))
    result = (
        "\n".join(
            " " * ((len(line) - len(line.lstrip())) // 4) + line.lstrip()
            for line in normalized.splitlines()
        )
        + "\n"
    )
    if ast.dump(ast.parse(code)) != ast.dump(ast.parse(result)):
        raise ValueError("Compaction changed Python semantics")
    # Template markers are forbidden even inside nested Python literals.
    result = result.replace("}}", "} }")
    if ast.dump(ast.parse(code)) != ast.dump(ast.parse(result)):
        raise ValueError("Template escaping changed Python semantics")
    return result


def failure_condition(node: str) -> dict:
    return {
        "logic": "or",
        "conditions": [
            {"variable": f"{node}_response.status", "operator": "equals", "operand": "error"},
            {"variable": f"{node}_failure_count", "operator": "greater_than", "operand": 0},
        ],
    }


def harden(workflow: dict, *, multi: bool) -> tuple[dict, list]:
    workflow = copy.deepcopy(workflow)
    nodes = workflow["nodes"]
    if "runtime_recovery" in nodes:
        raise ValueError("Already hardened")
    report = []
    for name, node in list(nodes.items()):
        if node.get("node_type") != "code":
            continue
        source = code_default(node)["default"]
        reduced = compact(source)
        code_default(node)["default"] = reduced
        node["transitions"].insert(
            0,
            {
                "target_node": "runtime_recovery",
                "description": "A failed controller must never reuse a stale bank-write route.",
                "condition": failure_condition(name),
            },
        )
        report.append(
            {
                "node": name,
                "before_bytes": len(source.encode()),
                "after_bytes": len(reduced.encode()),
                "ast_equivalent": True,
            }
        )
    farewell, terminal = ("farewell", "end_call") if multi else ("goodbye", "hang_up")
    recovery = RECOVERY.replace("STATE_KEY", repr("state" if multi else "call_state"))
    recovery = recovery.replace("FAREWELL_KEY", repr("farewell" if multi else "closing_message"))
    nodes["runtime_recovery"] = {
        "name": "runtime_recovery",
        "node_type": "code",
        "on_enter": {"type": "silent"},
        "passthrough": True,
        "passthrough_default_transition": "runtime_unavailable",
        "pre_actions": [
            {
                "type": "function",
                "function": {
                    "name": "recover_runtime_failure",
                    "handler": "__function__:code_exec",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "default": recovery},
                            "timeout_ms": {"type": "number", "default": 10000},
                        },
                    },
                },
            }
        ],
        "transitions": [
            {
                "target_node": "runtime_unavailable",
                "description": "No repeated code or bank request if recovery also fails.",
                "condition": failure_condition("runtime_recovery"),
            },
            {
                "target_node": farewell,
                "description": "Close with the observed bank outcome after recovery succeeds.",
                "condition": {
                    "variable": "runtime_recovery_response.status",
                    "operator": "equals",
                    "operand": "success",
                },
            },
            {
                "target_node": "runtime_unavailable",
                "description": "Unknown recovery result fails closed.",
                "condition": {"operator": "always"},
            },
        ],
    }
    nodes["runtime_unavailable"] = {
        "name": "runtime_unavailable",
        "node_type": "conversation",
        "on_enter": {
            "type": "tts",
            "text": "I am sorry, I cannot continue this call. Thank you. Goodbye.",
        },
        "task_message": (
            "The service is unavailable. Do not claim success or ask another question."
        ),
        "passthrough": True,
        "passthrough_default_transition": terminal,
        "disable_inactivity_monitor": True,
        "transitions": [
            {
                "target_node": terminal,
                "description": "Terminate if the small recovery step cannot run.",
                "condition": {"operator": "always"},
            }
        ],
    }
    return workflow, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=False)
    reports = []
    for name in ("single-product.json", "multi-product.json"):
        source = args.source / name
        workflow, changes = harden(
            json.loads(source.read_text()), multi=name == "multi-product.json"
        )
        target = args.destination / name
        write_json(target, workflow)
        reports.append(
            {
                "source": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "target": str(target),
                "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "changes": changes,
            }
        )
    write_json(
        args.report,
        {
            "source": "generic operational failure; no validation customer text used",
            "not_promoted": True,
            "references": reports,
        },
    )
    print(json.dumps(reports))


if __name__ == "__main__":
    main()
