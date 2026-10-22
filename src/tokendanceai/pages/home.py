from fastapi import APIRouter, Request

from tokendanceai.templating import templates

router = APIRouter()


@router.get("/", include_in_schema=False, name="home")
@router.get("/tokendanceai", include_in_schema=False, name="posts")
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")
