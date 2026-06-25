"""Shared MongoDB connection and config for Timetable scripts."""
import json
import os

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

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
_config_path = os.path.join(_project_root, "config.json")

with open(_config_path) as file:
    user = json.load(file)["user"]
