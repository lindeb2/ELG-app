"""MongoDB pipeline to fetch highscore inputs from aggregation docs."""

HIGHSCORE_FETCH_PIPELINE = [
    {
        "$match": {
            "$expr": {"$in": ["$_id", ["Highscores", "$$logUser", "Combined"]]},
        }
    },
    {
        "$group": {
            "_id": None,
            "docs": {"$push": {"k": "$_id", "v": "$$ROOT"}},
        }
    },
]
