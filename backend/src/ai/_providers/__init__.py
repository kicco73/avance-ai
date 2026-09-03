"""Concrete LLM providers and the cascade that fronts them — private to
the `ai` package. Nothing outside `ai/` imports from here: the only door
to a model is ai.AiService (see ai/__init__.py), which picks the driver
from config and owns every instance. tests/test_ai_package_boundary.py
enforces this."""
