from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from tokendanceai.pages import home
from tokendanceai.router import agent

BASE_DIR = Path(__file__).parent

app = FastAPI(title="TokenDance AI")

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

# HTML endpoint
app.include_router(home.router, tags=["homepage"])
# JSON endpoint
app.include_router(agent.router, prefix="/agent", tags=["agent"])
