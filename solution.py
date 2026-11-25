"""Compatibility entry point for task runners that import ``solution``."""
from app.rebooking.allocator import *  # noqa: F401,F403
from app.rebooking.service import RebookService
from app.rebooking.store import RebookStore

SolutionService = RebookService
