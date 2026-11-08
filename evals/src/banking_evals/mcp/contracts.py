"""Synthetic, in-memory MCP transport for isolated workflow evaluations."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from mcp import types


from banking_evals.resources import repository_root

ROOT = repository_root()


def _argument_schema(argument: ast.arg) -> dict[str, Any]:
    annotation = argument.annotation
    description = None
    if isinstance(annotation, ast.Subscript):
        if not isinstance(annotation.value, ast.Name) or annotation.value.id != "Annotated":
            raise ValueError("Unsupported contract annotation")
        if not isinstance(annotation.slice, ast.Tuple) or len(annotation.slice.elts) != 2:
            raise ValueError("Unsupported Annotated contract")
        annotation, description_node = annotation.slice.elts
        description = ast.literal_eval(description_node)
    nullable = False
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        if not isinstance(annotation.right, ast.Constant) or annotation.right.value is not None:
            raise ValueError("Unsupported contract annotation")
        nullable = True
        annotation = annotation.left
    if not isinstance(annotation, ast.Name) or annotation.id not in {"str", "bool"}:
        raise ValueError("Unsupported contract annotation")
    value_type = "string" if annotation.id == "str" else "boolean"
    schema: dict[str, Any] = {
        "anyOf": [{"type": value_type}, {"type": "null"}],
    } if nullable else {"type": value_type}
    if description is not None:
        schema["description"] = description
    return schema


def _tool_contract(function: ast.FunctionDef | ast.AsyncFunctionDef, decorator: ast.Call) -> types.Tool:
    metadata = {item.arg: ast.literal_eval(item.value) for item in decorator.keywords}
    properties = {
        argument.arg: _argument_schema(argument)
        for argument in function.args.args if argument.arg != "headers"
    }
    schema: dict[str, Any] = {
        "type": "object", "properties": properties, "additionalProperties": False,
    }
    default_start = len(function.args.args) - len(function.args.defaults)
    required = []
    for index, argument in enumerate(function.args.args):
        if argument.arg not in properties:
            continue
        if index < default_start:
            required.append(argument.arg)
        else:
            properties[argument.arg]["default"] = ast.literal_eval(
                function.args.defaults[index - default_start],
            )
    if required:
        schema["required"] = required
    return types.Tool(
        name=metadata["name"], description=metadata["description"], inputSchema=schema,
    )


def load_contracts(server: str) -> list[types.Tool]:
    """Read public tool declarations without importing database-backed services."""
    if server not in {"account", "transaction"}:
        raise ValueError("Unknown replay server")
    source = ROOT / "app" / "business-api" / server / "src" / f"banking_{server}" / "mcp_tools.py"
    contracts = []
    for function in ast.parse(source.read_text(encoding="utf-8")).body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in function.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "mcp" and decorator.func.attr == "tool"):
                contracts.append(_tool_contract(function, decorator))
    return contracts
