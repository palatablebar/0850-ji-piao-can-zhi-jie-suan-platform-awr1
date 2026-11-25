from app.rebooking.service import RebookService as RebookingAllocationService
from app.rebooking.service import RebookService
from app.rebooking.store import RebookStore


class RebookingAllocationService(RebookService):
    pass


__all__ = ["RebookService", "RebookStore", "RebookingAllocationService"]
