"""Local HTTP probe: shipped routers/read guards, no authentication assertion."""

import sys

sys.path.insert(0, "/app")

from fastapi import Depends, FastAPI

from app.common.publication_readiness import require_publication
from app.commune.router import router as commune
from app.vacance.router import router as vacance

app = FastAPI()
app.include_router(vacance, dependencies=[Depends(require_publication)])
app.include_router(commune)


@app.get("/probe/live")
def live():
    return {"probe": "ready"}
