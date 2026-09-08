"""Turning a project into a compiled package.

compiler.py does the work — literal Python for the project's structure,
its prompts apart in prompt.py, its archives verbatim in data/ — and
build_service.py is what the Build view calls. A module built locally is
written inside this package, so it imports as `build.<name>`.
"""
from .build_service import BUILD_DIR, BuildService, module_name_for
from .compiler import CompileError, compile_contents, compile_module, compile_package, verify_package

__all__ = [
    "BUILD_DIR", "BuildService", "CompileError", "compile_contents", "compile_module",
    "compile_package", "module_name_for", "verify_package",
]
