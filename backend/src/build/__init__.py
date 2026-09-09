"""Turning a project into a compiled package.

compiler.py does the work — literal Python for the project's structure,
its prompts apart in prompt.py, its archives verbatim in data/ — and
build_service.py is what the Build view calls.

The convention the writer and the reader must agree on — where a built
package lives and how it is imported back — is not here: it is
project/archive/packages.py, on the reading side, because the reader is
core and this package is not. A product serves a package it never
built, and importing anything from here would bring the compiler along
with it.
"""
from .build_service import BUILD_DIR, BuildService, module_name_for, published_revision_of
from .compiler import Compiler, CompileError, compile_contents, compile_package, verify_package

__all__ = [
    "BUILD_DIR", "BuildService", "CompileError", "Compiler", "compile_contents",
    "compile_package", "module_name_for", "published_revision_of", "verify_package",
]
