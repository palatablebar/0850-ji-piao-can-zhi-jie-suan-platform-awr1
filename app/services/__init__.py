"""Service-layer package for optional compatibility imports."""
from app.rebooking.service import RebookService

AllocationService = RebookService

__all__ = ["RebookService", "AllocationService"]
