"""Interpretato vs compilato, espressione per espressione, sullo stesso scope."""
import importlib, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, "src"); sys.path.insert(0, "bin")
import compile_automaton as C
from automaton.automaton_builder import AutomatonBuilder
from automaton.automaton import Automaton
from automaton.identifier_registry import IdentifierRegistry
from simpleeval import ModuleWrapper
from automaton.scope import EvaluationScope
import datetime as _dt

class Fake:
    """Un oggetto che risponde a qualunque attributo/chiamata, in modo
    deterministico: entrambi i percorsi ricevono lo stesso, quindi
    qualunque differenza nel risultato viene dalla valutazione."""
    def __init__(self, name="fake"): self._name = name
    def __getattr__(self, item): return Fake(f"{self._name}.{item}")
    def __call__(self, *a, **k): return f"<{self._name}()>"
    def __repr__(self): return f"<{self._name}>"

def scopes_for(automaton):
    # _TaskEval pretende un EvaluationScope, non un dict.
    users = {f: "x@y.z" if f == "email" else 1 for f in IdentifierRegistry.USER}
    envs = {k.name: "" for k in automaton.env_keys}
    base = dict(source=Fake("source"), chat=Fake("chat"), task=Fake("task"),
                session=Fake("session"), metric=Fake("metric"), automaton=Fake("automaton"),
                attachment=Fake("attachment"),
                datetime=ModuleWrapper(_dt, allowed_attrs={"datetime","timedelta","timezone"}))
    for label, sig in (("segnali a 0", {s.name: 0 for s in automaton.signals}),
                       ("segnali a 100", {s.name: 100 for s in automaton.signals}),
                       ("segnali None", {s.name: None for s in automaton.signals})):
        names = {**base, "signal": sig, "env": dict(envs), "user": dict(users)}
        yield label, EvaluationScope(names, automaton=automaton, state_key="", action_name="a")

def outcome(fn, *args):
    try: return ("ok", fn(*args))
    except Exception as exc: return ("raise", type(exc).__name__)

total = mismatches = 0
for project in sorted(Path("samples/projects").iterdir()):
    if not project.is_dir(): continue
    try: interpreted = AutomatonBuilder().build(C.read_project_contents(project))
    except Exception as exc: print(f"  {project.name}: non builda ({str(exc)[:40]}) — saltato"); continue
    out = Path(tempfile.mkdtemp())
    C.compile_package(project, "seamcheck", out)
    sys.path.insert(0, str(out))
    for stale in [m for m in sys.modules if m.startswith("seamcheck")]: del sys.modules[stale]
    compiled = importlib.import_module("seamcheck").AUTOMATON
    expressions, statements = C._collect_sources(interpreted)
    bad = 0
    for label, scope in scopes_for(interpreted):
        for expr in expressions:
            total += 1
            a, b = outcome(Automaton._evaluate_expression, expr, scope), outcome(type(compiled)._evaluate_expression, expr, scope)
            if a != b: bad += 1; print(f"    DIVERGE [{label}] {expr[:60]!r}: interpretato={a} compilato={b}")
        for stmt in statements:
            total += 1
            a, b = outcome(Automaton._evaluate_statement, stmt, scope), outcome(type(compiled)._evaluate_statement, stmt, scope)
            if a != b: bad += 1; print(f"    DIVERGE [{label}] {stmt[:60]!r}: interpretato={a} compilato={b}")
        for expr in expressions:
            total += 1
            a, b = outcome(Automaton._eval_trigger, expr, scope), outcome(type(compiled)._eval_trigger, expr, scope)
            if a != b: bad += 1; print(f"    DIVERGE trigger [{label}] {expr[:50]!r}: {a} vs {b}")
    mismatches += bad
    print(f"  {project.name}: {len(expressions)} espressioni, {len(statements)} statement — {'OK' if not bad else str(bad)+' DIVERGENZE'}")
    sys.path.remove(str(out)); shutil.rmtree(out, ignore_errors=True)
print(f"\n{total} confronti, {mismatches} divergenze")
