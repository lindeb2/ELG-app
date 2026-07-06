# Development Notes

## Completed
- [X]  Add updates in sync
  -  [X] Resume tokens
- [X]  Handle DB goals with 0
- [X]  Dynamic slide creation
- [X]  Eliminate desync after init
- [X]  $currentDate vs $$NOW
- [X]  `get_online_users_info` DB-eval
- [X]  0X vs X fix for logs
   - [X]  Iso-week-formating
- [X]  Ensure client is on the correct week
- [X]  Merged s4_update_[]_goal method
- [X]  handle_arrow direct return?
- [X]  `_calculate_week_info` output
- [X]  Goal color function
- [X]  **Hours Graph:** Add Goal Lines?
- [X]  **Days Chart:** Add Goal Lines?
- [X]  **Team Hours Bar:** Add Goal Line?
- [X]  S1 Highscore
- [X]  Stats_viewer
- [X]  Notification system

## Moved to Issues
- [ ]  `self.days_chart_data_row.bind("<Configure>", update_day_bars)`? (Moved to issue)
- [ ]  Watcher-handler-update-limit (Moved to issue)
- [ ]  Optimize db
   - [X]  Consolidate watchers
   - [X]  Implement projections
   - [X]  Index logs
   - [X]  Slide 5
      - [X]  Watcher
   - [X] Move `Update_one` into worker-thread
   - [ ]  Round-trips
     - [X] Batch init / DB-init function
     - [ ] Watcher Pipeline (Moved to issue)
   - [X]  Rework DB with ISODate instead of multiple fields

## To-Do
- [ ]  DRY (Don't Repeat Yourself) / First runs
- [ ]  Ponder: Args vs Instance variables
- [ ]  Merge into one app
   - [X]  Correct local data storage
   - [X]  Integrate all parts
   - [ ]  Dry function calls between parts
   - [X]  On close logic
   - [X]  Windows topbar after maximized
   - [X]  Sidebar gone at maximized
   - [X]  Timetable widget
   - [X]  Disable OS to shut down
   - [X]  Minimize app to system tray on close?
   - [X]  Icon
   - [X]  Auto-update
- [X]  Logic if new week
- [ ]  Auto log and record during meetings
- [ ]  Implement Color Themes
- [ ]  Notification settings