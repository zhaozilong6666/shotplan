"""shotplan: an evaluable, constraint-aware planning agent.

The core package deliberately depends only on the Python standard library.
Framework integrations (FastAPI, web UI) live in optional layers.
"""

__version__ = "0.5.0"

from .pipeline import PlanResult, run_plan

__all__ = ["PlanResult", "run_plan", "__version__"]
