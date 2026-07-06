"""Shared MongoDB connection and config for Timetable scripts."""
from __future__ import annotations

import sys
from pathlib import Path

import certifi
from app_secrets import get_mongodb_uri
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

from runtime_paths import is_packaged_build


def _mongodb_tls_ca_file() -> str:
    """Resolve CA bundle for TLS; Nuitka bundles certifi next to the executable."""
    if is_packaged_build():
        bundled = Path(sys.executable).resolve().parent / "certifi" / "cacert.pem"
        if bundled.is_file():
            return str(bundled)
    return certifi.where()


client = MongoClient(
    get_mongodb_uri(),
    server_api=ServerApi("1"),
    tlsCAFile=_mongodb_tls_ca_file(),
)
db = client["ELG-Database"]
collection = db["Timetable"]
aggregations = db["Timetable Aggregations"]
status_meeting = db["Status Meeting"]

user = ""


def get_user() -> str:
    from app_config import read_config

    return read_config().get("user", "")


def sync_user() -> None:
    global user
    user = get_user()
