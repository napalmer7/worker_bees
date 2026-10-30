import pytest

from worker_bees.analysis import build_index
from worker_bees.queries import Investigation


def targets(index):
    symbols = {symbol.id: symbol.qualified_name for symbol in index.symbols}
    return {call.expression: symbols.get(call.target_id) for call in index.calls}


def test_fixture_aliases_relative_imports_nested_and_async(make_repo):
    index = build_index(make_repo(fixture=True))
    assert index.snapshot.status == "complete"
    found = targets(index)
    assert found["normalize"] == "shop.pricing.normalize"
    assert found["calculate"] == "shop.pricing.total"
    assert found["prices.total"] == "shop.pricing.total"
    assert found["helper"] == "shop.service.nested.helper"
    assert found["handler"] is None
    assert found["self.quote"] is None
    assert found["max"] is None
    assert any(s.kind == "async_function" for s in index.symbols)
    assert any(s.kind == "method" for s in index.symbols)


@pytest.mark.parametrize(
    "body",
    [
        "def caller(target):\n    target()",
        "def caller():\n    target()\n    target = other",
        "def caller():\n    for target in []:\n        pass\n    target()",
        "def caller():\n    try:\n        pass\n    except Exception as target:\n        target()",
        "def caller(value):\n    match value:\n        case {'x': target}:\n            target()",
        "target = other\ntarget()",
        "del target\ntarget()",
        "def caller():\n    global target\n    target()",
        "def caller():\n    return [target() for target in []]",
        "def caller():\n    return (lambda target: target())",
        "if enabled:\n    def target():\n        pass\ntarget()",
        "def mutate():\n    global target\n    target = other\ntarget()",
    ],
)
def test_shadowed_or_unsupported_calls_stay_unknown(make_repo, body):
    source = "def target():\n    pass\n" + body + "\n"
    index = build_index(make_repo({"sample.py": source}))
    calls = [call for call in index.calls if call.expression == "target"]
    assert calls, "test must exercise a target call"
    assert all(call.target_id is None for call in calls)


def test_nonlocal_rebinding_invalidates_outer_target(make_repo):
    index = build_index(
        make_repo(
            {
                "a.py": """
def outer():
    def target():
        pass
    def mutate():
        nonlocal target
        target = other
    target()
"""
            }
        )
    )
    assert targets(index)["target"] is None


@pytest.mark.parametrize(
    "definition",
    [
        "def target():\n    pass\ntarget = other",
        "@decorator\ndef target():\n    pass",
        "if enabled:\n    def target():\n        pass",
        "from external import *\ndef target():\n    pass",
    ],
)
def test_unstable_import_exports_remain_unknown(make_repo, definition):
    index = build_index(
        make_repo(
            {
                "a.py": definition,
                "b.py": "from a import target\ntarget()\n",
            }
        )
    )
    assert targets(index)["target"] is None


def test_module_alias_and_namespace_mutation(make_repo):
    index = build_index(
        make_repo(
            {
                "pkg/__init__.py": "",
                "pkg/tools.py": "def run():\n    pass\n",
                "a.py": "import pkg.tools as t\nt.run()\n",
                "b.py": "import pkg.tools\npkg.tools.run()\n",
                "c.py": "import pkg.tools as t\nt.run = replacement\nt.run()\n",
            }
        )
    )
    by_path = {call.path: call for call in index.calls}
    assert by_path["a.py"].target_id is not None
    assert by_path["b.py"].target_id is not None
    assert by_path["c.py"].target_id is None


def test_method_does_not_close_over_class_namespace(make_repo):
    index = build_index(
        make_repo(
            {
                "a.py": """
class Example:
    def target():
        pass
    target()
    def run(self):
        target()
"""
            }
        )
    )
    assert all(call.target_id is None for call in index.calls)


def test_imported_same_name_symbols_are_not_guessed(make_repo):
    index = build_index(
        make_repo(
            {
                "a.py": "def run():\n    pass",
                "b.py": "def run():\n    pass",
                "c.py": "def caller(obj):\n    obj.run()",
            }
        )
    )
    assert targets(index)["obj.run"] is None


def test_duplicate_module_definitions_are_ambiguous(make_repo):
    index = build_index(
        make_repo(
            {
                "a.py": "def run():\n    pass",
                "src/a.py": "def run():\n    pass",
                "b.py": "from a import run\nrun()",
            }
        )
    )
    assert targets(index)["run"] is None


def test_duplicate_module_roots_do_not_resolve_a_unique_looking_export(make_repo):
    index = build_index(
        make_repo(
            {
                "a.py": "def run():\n    pass",
                "src/a.py": "# Different module chosen by a different import path\n",
                "b.py": "from a import run\nrun()",
            }
        )
    )
    assert targets(index)["run"] is None


def test_generic_type_parameter_scopes_are_explicitly_unsupported(make_repo):
    index = build_index(
        make_repo(
            {
                "a.py": """
def target():
    pass
def caller[target]():
    target()
"""
            }
        )
    )
    assert targets(index)["target"] is None


def test_nonlocal_assignment_before_outer_definition_invalidates_candidate(make_repo):
    index = build_index(
        make_repo(
            {
                "a.py": """
def outer():
    def mutate():
        nonlocal target
        target = other
    def target():
        pass
    target()
"""
            }
        )
    )
    assert targets(index)["target"] is None


def test_context_character_budget_applies_to_single_long_lines(make_repo):
    source = 'def large():\n    return "' + "x" * 15_000 + '"\n'
    query = Investigation(build_index(make_repo({"a.py": source})))
    context = query.context("a.large")
    assert len(context.source) == 12_000
    assert context.truncated


def test_queries_provide_context_callers_impact_and_bounds(make_repo):
    investigation = Investigation(build_index(make_repo(fixture=True)))
    result = investigation.find("total")
    assert result.total == 1
    symbol = result.items[0]
    callers = investigation.references(symbol.id)
    assert callers.total == 3
    assert all(item.path == "shop/service.py" for item in callers.items)
    context = investigation.context(symbol.id, max_lines=1)
    assert context.source.startswith("def total") and context.truncated
    affected = investigation.impact("shop.pricing.normalize", depth=2)
    assert {item.qualified_name for item in affected.items} == {
        "shop.pricing.total",
        "shop.service.quote",
        "shop.service.quote_async",
        "shop.service.nested.helper",
    }
    assert investigation.find("shop", limit=1).truncated
    assert investigation.unresolved().total >= 3
    with pytest.raises(ValueError):
        investigation.find("total", limit=0)
    with pytest.raises(ValueError):
        investigation.context("missing")
    with pytest.raises(ValueError):
        investigation.impact(symbol.id, depth=11)
