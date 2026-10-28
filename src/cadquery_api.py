"""
Knowledge about the real CadQuery API, read from the installed library so it always matches the version that runs the scripts.

1. check_api_calls: static check of generated code. Follows chains such as cq.Workplane(...).box(...).faces(...) and checks
   that every method exists and accepts the given arguments, before anything is executed
2. explain_runtime_error: turns a TypeError/AttributeError from execution into the real signature and documentation
3. method_card / api_reference: short reference text of methods (signature + summary), used in prompts and feedback
"""

import ast
import difflib
import inspect
import re
import typing
from functools import cache

import cadquery as cq

# Classes a generated script can start a chain from: cq.<name>(...)
ROOT_CLASSES: dict[str, type] = {"Workplane": cq.Workplane, "Sketch": cq.Sketch}

# Methods that are always worth showing: they position things, which small models get wrong most often
CORE_METHODS = [
    (cq.Workplane, "workplane"),
    (cq.Workplane, "center"),
    (cq.Workplane, "pushPoints"),
    (cq.Workplane, "box"),
    (cq.Workplane, "extrude"),
    (cq.Workplane, "hole"),
    (cq.Workplane, "union"),
]

MAX_SUMMARY_CHARS = 250

ERROR_CALL_PATTERN = re.compile(r"\b([A-Z]\w*)\.(\w+)\(\)")
ERROR_ATTRIBUTE_PATTERN = re.compile(r"'(\w+)' object has no attribute '(\w+)'")
ERROR_KEYWORD_PATTERN = re.compile(r"unexpected keyword argument '(\w+)'")


@cache
def _signature(cls: type, name: str) -> inspect.Signature | None:
    try:
        return inspect.signature(getattr(cls, name))
    except (TypeError, ValueError):
        return None


def _is_bound_to_class(cls: type, name: str) -> bool:
    """Static and class methods take no instance as first argument."""
    return isinstance(inspect.getattr_static(cls, name, None), staticmethod | classmethod)


@cache
def public_methods(cls: type) -> list[str]:
    return [name for name in dir(cls) if not name.startswith("_") and callable(getattr(cls, name, None))]


def _format_parameter(parameter: inspect.Parameter) -> str:
    if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
        return f"*{parameter.name}"
    if parameter.kind is inspect.Parameter.VAR_KEYWORD:
        return f"**{parameter.name}"
    text = parameter.name
    if parameter.default is not inspect.Parameter.empty:
        text += f"={parameter.default!r}"
    if typing.get_origin(parameter.annotation) is typing.Literal:
        text += f" (one of {', '.join(map(repr, typing.get_args(parameter.annotation)))})"
    return text


def format_signature(cls: type, name: str) -> str:
    """e.g. 'Workplane.center(x, y)'. Type annotations are left out except the allowed values of Literal arguments."""
    signature = _signature(cls, name)
    label = cls.__name__ if name == "__init__" else f"{cls.__name__}.{name}"
    if signature is None:
        return f"{label}(...)"
    parameters = [p for p in signature.parameters.values() if p.name not in {"self", "cls"}]
    return f"{label}({', '.join(_format_parameter(p) for p in parameters)})"


def _documentation(cls: type, name: str) -> tuple[str, list[str]]:
    """First paragraph of the docstring, and its ':param name: text' lines."""
    doc = inspect.getdoc(getattr(cls, name)) or ""
    summary_lines = []
    for line in doc.splitlines():
        if not line.strip() or line.lstrip().startswith(":"):
            break
        summary_lines.append(line.strip())
    summary = " ".join(summary_lines)
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "..."
    parameters = [f"{param}: {' '.join(text.split())}" for param, text in re.findall(r":param (\w+):\s*(.+)", doc)]
    return summary, parameters


def method_card(cls: type, name: str, detailed: bool = False) -> str:
    """Signature and summary; with detailed=True also the documentation of each argument."""
    summary, parameters = _documentation(cls, name)
    lines = [format_signature(cls, name)]
    if summary:
        lines.append(f"    {summary}")
    if detailed:
        lines += [f"    - {parameter}" for parameter in parameters]
    return "\n".join(lines)


def _close_matches(word: str, options: list[str], cutoff: float = 0.6) -> list[str]:
    """Like difflib.get_close_matches but case-insensitive, returning the original spelling."""
    by_lower = {option.lower(): option for option in options}
    return [by_lower[match] for match in difflib.get_close_matches(word.lower(), list(by_lower), n=3, cutoff=cutoff)]


def _suggestions(cls: type, word: str, parameters: list[str] | None = None) -> str:
    """'Did you mean ...' text with the cards of the suggested methods."""
    hints = []
    close_parameters = _close_matches(word, parameters or [])
    if close_parameters:
        hints.append(f"Did you mean the argument {' or '.join(repr(p) for p in close_parameters)}?")
    close_methods = _close_matches(word, public_methods(cls), cutoff=0.7)
    if close_methods:
        hints.append(f"Did you mean the method {' or '.join(f'.{m}()' for m in close_methods)}?")
        hints += [method_card(cls, method) for method in close_methods[:2]]
    return "\n".join(hints)


class _ChainChecker(ast.NodeVisitor):
    """Infers which CadQuery class each expression has, and checks every method call made on a known class."""

    def __init__(self) -> None:
        self.known: dict[str, type] = {}  # variable name -> CadQuery class
        self.errors: list[str] = []
        self.used: list[tuple[type, str]] = []

    def check(self, tree: ast.AST) -> None:
        # ast.walk visits the top-level statements in order first, so variables are known before they are used
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                cls = self.resolve(node.value)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._remember(target.id, cls)
            elif isinstance(node, ast.AnnAssign | ast.AugAssign) and node.value is not None:
                cls = self.resolve(node.value)
                if isinstance(node.target, ast.Name):
                    self._remember(node.target.id, cls)
            elif isinstance(node, ast.Expr | ast.Return) and node.value is not None:
                self.resolve(node.value)

    def _remember(self, name: str, cls: type | None) -> None:
        if cls is None:
            self.known.pop(name, None)
        else:
            self.known[name] = cls

    def resolve(self, expr: ast.expr) -> type | None:
        if isinstance(expr, ast.Name):
            return self.known.get(expr.id)
        if not isinstance(expr, ast.Call):
            return None

        # Arguments can hold their own chains, e.g. .union(cq.Workplane(...).box(...))
        for argument in [*expr.args, *(keyword.value for keyword in expr.keywords)]:
            self.resolve(argument)

        func = expr.func
        if not isinstance(func, ast.Attribute):
            return None
        if isinstance(func.value, ast.Name) and func.value.id == "cq":
            cls = ROOT_CLASSES.get(func.attr)
            if cls is not None:
                self._check_arguments(cls, "__init__", expr)
            return cls
        return self._resolve_method_call(func, expr)

    def _resolve_method_call(self, func: ast.Attribute, expr: ast.Call) -> type | None:
        owner = self.resolve(func.value)
        if owner is None:
            return None
        if not hasattr(owner, func.attr):
            message = f"Line {expr.lineno}: {owner.__name__} has no method `.{func.attr}()`."
            hint = _suggestions(owner, func.attr)
            self.errors.append(f"{message}\n{hint}" if hint else message)
            return None

        self.used.append((owner, func.attr))
        self._check_arguments(owner, func.attr, expr)
        return self._returned_class(owner, func.attr)

    def _check_arguments(self, cls: type, name: str, call: ast.Call) -> None:
        if any(isinstance(arg, ast.Starred) for arg in call.args) or any(keyword.arg is None for keyword in call.keywords):
            return  # *args / **kwargs: the real arguments are unknown
        signature = _signature(cls, name)
        if signature is None:
            return

        positional = [None] * len(call.args)
        if not _is_bound_to_class(cls, name):
            positional.insert(0, None)  # self
        keywords = {keyword.arg: None for keyword in call.keywords if keyword.arg is not None}
        try:
            signature.bind(*positional, **keywords)
        except TypeError as exc:
            label = cls.__name__ if name == "__init__" else f"{cls.__name__}.{name}"
            accepts_any_keyword = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
            unknown = [] if accepts_any_keyword else [keyword for keyword in keywords if keyword not in signature.parameters]
            if unknown:
                message = f"Line {call.lineno}: {label}() has no argument '{unknown[0]}'."
                hint = _suggestions(cls, unknown[0], list(signature.parameters))
            else:
                message = f"Line {call.lineno}: wrong arguments for {label}(): {exc}."
                hint = ""
            parts = [message, hint, "Real signature, use ONLY these arguments:", method_card(cls, name, detailed=True)]
            self.errors.append("\n".join(part for part in parts if part))

    @staticmethod
    def _returned_class(owner: type, name: str) -> type | None:
        signature = _signature(owner, name)
        if signature is None:
            return None
        returned = signature.return_annotation
        if isinstance(returned, typing.TypeVar):  # chainable methods are annotated `-> T`
            return owner
        if returned in ROOT_CLASSES.values():
            return returned
        return None  # e.g. .val() leaves the fluent API: stop checking this chain


def check_api_calls(tree: ast.AST) -> list[str]:
    """Errors for calls to CadQuery methods that do not exist or do not accept the given arguments."""
    checker = _ChainChecker()
    checker.check(tree)
    return checker.errors


def methods_used(code: str) -> list[tuple[type, str]]:
    """(class, method) for every CadQuery method call that can be resolved, in order of first use."""
    checker = _ChainChecker()
    checker.check(ast.parse(code))
    return list(dict.fromkeys(checker.used))


def api_reference(codes: list[str], limit: int = 12) -> str:
    """Cards for the core positioning methods and the methods used in the given scripts (e.g. retrieved recipes)."""
    methods = list(dict.fromkeys([*CORE_METHODS, *(method for code in codes for method in methods_used(code))]))
    return "\n".join(method_card(cls, name) for cls, name in methods[:limit])


def _cadquery_class(name: str) -> type | None:
    obj = getattr(cq, name, None)
    return obj if isinstance(obj, type) else None


def explain_runtime_error(error: str) -> str | None:
    """Reference text for a CadQuery TypeError/AttributeError raised while executing a script, or None."""
    lines = error.strip().splitlines()
    last_line = lines[-1] if lines else ""

    attribute = ERROR_ATTRIBUTE_PATTERN.search(last_line)
    if attribute and (cls := _cadquery_class(attribute[1])):
        hint = _suggestions(cls, attribute[2])
        return f"{cls.__name__} has no attribute `.{attribute[2]}`." + (f"\n{hint}" if hint else "")

    call = ERROR_CALL_PATTERN.search(last_line)
    if call and (cls := _cadquery_class(call[1])) and hasattr(cls, call[2]):
        parts = [f"Real signature of {cls.__name__}.{call[2]}(), use ONLY these arguments:", method_card(cls, call[2], detailed=True)]
        keyword = ERROR_KEYWORD_PATTERN.search(last_line)
        if keyword:
            signature = _signature(cls, call[2])
            parts.append(_suggestions(cls, keyword[1], list(signature.parameters) if signature else []))
        return "\n".join(part for part in parts if part)

    return None
