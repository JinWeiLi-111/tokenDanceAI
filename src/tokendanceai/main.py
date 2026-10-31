from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from tokendanceai.pages import home
from tokendanceai.router import agent, servers, sessions, user

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
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(user.router, prefix="/api/user", tags=["user"])
app.include_router(servers.router, prefix="/api/servers", tags=["servers"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
