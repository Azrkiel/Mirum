"""One expensive glider/CEM job per API process; synchronous event-loop claims."""
from fastapi import HTTPException


class SolverSlot:
    def __init__(self):
        self.owner = None

    def check(self):
        if self.owner is not None:
            raise HTTPException(429, 'A glider or CEM task is running; retry after it completes')

    def claim(self, owner):
        self.check()
        self.owner = owner

    def release(self, owner):
        if self.owner is owner:
            self.owner = None


slot = SolverSlot()
