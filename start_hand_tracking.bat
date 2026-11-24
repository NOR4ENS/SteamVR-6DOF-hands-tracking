@echo off
chcp 65001 >nul
cd /d "E:\VR"
"E:\VR\.venv\Scripts\python.exe" "E:\VR\vr_hand_tracking.py" %*
if errorlevel 1 pause
