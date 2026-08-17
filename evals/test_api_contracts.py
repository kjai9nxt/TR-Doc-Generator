"""Every field a request handler reads must exist on its request model.

    python -m evals.test_api_contracts        # no API key, no server, ~1 second

WHY THIS EXISTS. A handler read `body.course` while its model, GuidedStartBody, had no
such field — so every attempt to start a generation died with

    AttributeError on /api/guided/start: 'GuidedStartBody' object has no attribute 'course'

The feature had been written and the model edit was lost, and nothing noticed: the
Python suite never calls the endpoint (it would start a real generation), and the UI
harness stubs the response. The mismatch is entirely visible in the source, though —
the handler names the attribute and the model declares its fields — so it can be
checked without running anything.

This walks the AST of server.py, finds every handler taking a Pydantic model, and
compares the attributes it reads against the model's declared fields (inherited ones
included). It is a static check: no server, no database, no network.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


src = (ROOT / "server.py").read_text(encoding="utf-8")
tree = ast.parse(src)

# 1. Every BaseModel and the fields it declares (following base classes).
models: dict[str, set] = {}
bases: dict[str, list] = {}
for node in tree.body:
    if not isinstance(node, ast.ClassDef):
        continue
    base_names = [b.id for b in node.bases if isinstance(b, ast.Name)]
    if "BaseModel" not in base_names and not any(b in models for b in base_names):
        continue
    fields = {n.target.id for n in node.body
              if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    models[node.name] = fields
    bases[node.name] = base_names


def fields_of(name: str) -> set:
    out = set(models.get(name, set()))
    for b in bases.get(name, []):
        if b in models:
            out |= fields_of(b)
    return out


print(f"\n== request models found: {len(models)} ==")
print("   " + ", ".join(sorted(models)))

# 2. Every function whose parameter is annotated with one of those models, and the
#    attributes it reads off that parameter.
problems = []
checked = 0
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for arg in list(node.args.args) + list(node.args.kwonlyargs):
        ann = arg.annotation
        model = ann.id if isinstance(ann, ast.Name) else None
        if model not in models:
            continue
        checked += 1
        declared = fields_of(model)
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                    and sub.value.id == arg.arg):
                if sub.attr not in declared and not sub.attr.startswith("model_"):
                    problems.append(
                        f"{node.name}() reads {arg.arg}.{sub.attr}, but {model} declares "
                        f"only: {', '.join(sorted(declared))}")

print(f"\n== handlers taking a request model: {checked} ==")
check("every field a handler reads is declared on its model",
      not problems, "\n        " + "\n        ".join(problems))

# 3. The specific regression, named, so its absence is obvious in the output.
for model, field in (("GuidedStartBody", "course"), ("GuidedStartBody", "team_id"),
                     ("CurriculumSaveBody", "course"), ("IngestBody", "course"),
                     ("SessionSettingsBody", "session_no")):
    check(f"{model}.{field} exists", field in fields_of(model))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
