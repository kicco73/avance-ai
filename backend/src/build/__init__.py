"""Turning a project into a compiled package.

compiler.py does the work — literal Python for the project's structure,
its prompts apart in prompt.py, its archives verbatim in data/ —
build_service.py is what the Build view calls, and apps.py owns the one
convention the writer and the reader must agree on: where a built
package lives and how it is imported back.
"""
from .apps import PackageError, import_automaton, package_dir, staging_dir
from .build_service import BUILD_DIR, BuildService, module_name_for, published_revision_of
from .compiler import Compiler, CompileError, compile_contents, compile_package, verify_package

__all__ = [
    "BUILD_DIR", "BuildService", "CompileError", "Compiler", "PackageError", "compile_contents",
    "compile_package", "import_automaton", "module_name_for", "package_dir", "published_revision_of",
    "staging_dir", "verify_package",
]
