import sys
import argparse
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

MONGO_URI = "mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/?retryWrites=true&w=majority&appName=ELG-timetable"

def get_db_name():
    """
    Determines DB name via CLI argument or Interactive input.
    """
    parser = argparse.ArgumentParser(description="Initialize the ELG App Database.")
    parser.add_argument("--name", type=str, help="The name of the new database to create.")
    args = parser.parse_args()

    if args.name:
        return args.name

    # Interactive fallback
    print("--- ELG Database Initialization ---")
    while True:
        name = input("Enter the name for the new database (e.g., ELG-Prod-v1): ").strip()
        if name:
            return name
        print("Database name cannot be empty.")

def init_database():
    db_name = get_db_name()

    try:
        client = MongoClient(MONGO_URI)
        client.admin.command('ping')
    except (ConnectionFailure, OperationFailure) as e:
        print(f"Error connecting to MongoDB: {e}")
        sys.exit(1)

    # 1. Check if DB exists
    existing_dbs = client.list_database_names()
    if db_name in existing_dbs:
        print(f"\n[!] ABORTING: Database '{db_name}' already exists.")
        print("    To prevent accidental data loss, this script will not overwrite existing databases.")
        print("    Please choose a different name or delete the existing database manually.")
        sys.exit(1)

    print(f"\n[+] Creating Database: {db_name}...")
    db = client[db_name]

    # 2. Create Collections & Initial Data ---

    # A. Collection: Status Meeting
    print("    -> Initializing 'Status Meeting' collection...")
    status_col = db['Status Meeting']

    # A1: State
    status_col.insert_one({
        "_id": "State",
        "slide": 0,
        "week": None
    })

    # A2: Goals
    status_col.insert_one({
        "_id": "Goals"
    })

    # A3: Users
    status_col.insert_one({
        "_id": "Users",
        "data": {}
    })

    # A4: Discussion Points
    status_col.insert_one({
        "_id": "Discussion Points"
    })

    # A5: End Strings
    status_col.insert_one({
        "_id": "End Strings",
        "lock_timestamp": None,
        "data": {}
    })

    # B. Collection: Timetable Aggregations
    print("    -> Initializing 'Timetable Aggregations' collection...")
    agg_col = db['Timetable Aggregations']

    # B1: Highscores
    agg_col.insert_one({
        "_id": "Highscores",
        "Global": {},
        "Combined": {}
    })

    # C. Collection: Timetable
    print("    -> Initializing 'Timetable' collection...")
    db.create_collection("Timetable")

    print(f"\n[SUCCESS] Database '{db_name}' initialized successfully.")
    print("You can now update your 'config.json' or connection string to use this database.")

if __name__ == "__main__":
    init_database()