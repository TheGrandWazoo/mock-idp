from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

# Resolves to src/playground.html locally, /app/playground.html in Docker
_PLAYGROUND_PATH = Path(__file__).parent.parent.parent / "playground.html"


@router.get("/", response_class=HTMLResponse)
async def playground():
    if not _PLAYGROUND_PATH.exists():
        return HTMLResponse(
            "<h1>Mock IDP</h1>"
            "<p>Playground HTML not bundled. "
            "See /docs for the API, /debug/identities for the loaded store.</p>"
        )
    return _PLAYGROUND_PATH.read_text()
