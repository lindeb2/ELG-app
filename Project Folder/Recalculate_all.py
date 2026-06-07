from recalculate import (
    recalculate_all_aggregations,
    recalculate_all_highscores,
    recalculate_all_streaks,
)

print("Starting recalculation")
recalculate_all_aggregations()
recalculate_all_streaks()
recalculate_all_highscores()
print("Recalculation complete!")
