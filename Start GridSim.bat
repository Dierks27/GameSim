@echo off
rem ---------------------------------------------------------------------------
rem  Start GridSim: launches the gridsim GUI (Streamlit) and opens your browser.
rem  Double-click this file, or make a desktop shortcut to it.
rem  Phone on the same Wi-Fi: http://<this PC's LAN IP>:8501
rem  Close this window (or press Ctrl+C) to stop the GUI.
rem ---------------------------------------------------------------------------

rem Run from the folder this .bat lives in (the GameSim repo root), so
rem .streamlit\config.toml, config.yaml and the data cache are found.
cd /d "%~dp0"

rem Use the project's virtual environment if it exists.
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.11+ from python.org and run the README install steps.
    pause
    exit /b 1
)

title GridSim
echo Starting GridSim on http://localhost:8501  (phone: http://^<PC-LAN-IP^>:8501)
python -m streamlit run app\streamlit_app.py --server.address 0.0.0.0 --server.port 8501

rem Keep the window open if Streamlit exits with an error, so the message can be read.
if errorlevel 1 pause
