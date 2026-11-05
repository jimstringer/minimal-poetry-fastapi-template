# The configuration for the app
import os
from pathlib import Path
from fastapi import Request

# Where we store the database
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATABASE_PATH = DATA_DIR / "thedb.db"

# Auth
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is not set") 
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days

def get_client_ip(request: Request) -> str:
    """Return the client IP for rate limiting and auth logs: the socket peer.

    No header is read here, because every header is client-controlled. Behind a
    trusted reverse proxy the peer is already the real client: uvicorn's
    proxy-headers middleware rewrites it for peers listed in FORWARDED_ALLOW_IPS
    (set from SHELF_TRUST_PROXY by entrypoint.sh), walking X-Forwarded-For from
    the right and skipping trusted hops.
    """
    return request.client.host if request.client else "unknown"

