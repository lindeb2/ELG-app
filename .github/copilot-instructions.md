# Copilot Instructions for Timetable Project

## Overview
This project is a Python desktop application suite for time tracking, meeting management, and statistics visualization, built with `customtkinter` and MongoDB. It includes several main apps:
- `Timetable.py` / `Timetable_new.py`: Core time tracking and logging
- `meeting_app.py`: Weekly status meeting manager
- `meeting_point_manager.py`: Meeting points and discussion management
- `stats_viewer.py`, `Recalculate_all.py`: Data aggregation and statistics
- Custom widgets: `CTkFlexToolTip`, `CTkPieChart`, `CtkSmartScrollableFrame`

## Architecture & Data Flow
- **UI**: All major apps use `customtkinter` for a modern dark-themed interface. Custom widgets are imported from their respective folders.
- **Database**: MongoDB Atlas is used for all persistent data. Connection details are hardcoded in each main file. Collections include `Timetable`, `Status Meeting`, `Timetable Aggregations`, etc.
- **Config**: User info (e.g., author) is loaded from the root `config.json` at runtime.
- **Aggregation**: Data is aggregated by day, week, month, and year using functions in `Timetable_new.py`. Highscore and summary recalculation is triggered via `Recalculate_all.py`.
- **External Integration**: Some notification logic is present for Google App Engine (see `GAE_URL` in `Timetable_new.py`).

## Developer Workflows
- **Build**: Use `pyinstaller` with `Timetable.spec` to build the main executable. Output is in `Project Folder/dist/Timetable.exe`.
- **Recalculation**: Run `Recalculate_all.py` to rebuild all aggregations and highscores from raw data.
- **Config**: Update `config.json` for user-specific settings (e.g., author name).
- **Custom Widgets**: Extend or modify widgets in their respective folders for UI changes.

## Project-Specific Patterns
- **MongoDB Usage**: All database access uses the same Atlas URI and `pymongo`. Collections are referenced directly in each file.
- **UI Theme**: Always set `ctk.set_appearance_mode("Dark")` at the start of each app.
- **Config Path**: Apps locate `config.json` by traversing up from the executable/script location.
- **Aggregation Functions**: See `Timetable_new.py` for canonical aggregation logic. Always use these for summary/statistics updates.
- **Custom Widgets**: Import custom widgets using their package names (e.g., `from CTkPieChart import CTkPieChart`).

## Key Files & Directories
- `Project Folder/Timetable.py`: Aggregation, highscore, and notification logic
- `Project Folder/meeting_app.py`: Status meeting workflow and UI
- `Project Folder/meeting_point_manager.py`: Meeting points management
- `Project Folder/CTkFlexToolTip/`, `CTkPieChart/`, `CtkSmartScrollableFrame/`: Custom widget implementations
- `config.json`: User/author config
- `Timetable.spec`: PyInstaller build config

## Example: Rebuilding Aggregations
```shell
python Project\ Folder/Recalculate_all.py
```

## Example: Building Executable
```shell
pyinstaller Project\ Folder/Timetable.spec
```

## Example: Custom Widget Usage
```python
from CTkPieChart import CTkPieChart
chart = CTkPieChart(...)
```

---
If any section is unclear or missing, please provide feedback so this guide can be improved.
