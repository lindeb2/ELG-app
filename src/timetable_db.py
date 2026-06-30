"""Shared MongoDB connection and config for Timetable scripts."""
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

client = MongoClient(
    "mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/?retryWrites=true&w=majority&appName=ELG-timetable",
    server_api=ServerApi("1"),
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
