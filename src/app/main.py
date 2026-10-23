
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT_PATH = Path(__file__).parent
static_folder = ROOT_PATH / "static"
templates_folder = ROOT_PATH / "templates"

# ruff error: use Pathlib instead of os.path
# import os
# script_dir = os.path.dirname(__file__)
# st_abs_file_path = os.path.join(script_dir, "static/")
# tp_abs_file_path = os.path.join(script_dir, "templates/")

app = FastAPI()
app.mount("/static", StaticFiles(directory=static_folder), name="static")
app.add_middleware(GZipMiddleware)

templates = Jinja2Templates(directory=templates_folder)


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="base.html", context={})
