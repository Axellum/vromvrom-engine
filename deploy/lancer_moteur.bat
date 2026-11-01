@echo off
rem -u : sortie non bufferisée (sinon un démarrage bloqué laisse un log vide).
cd /d "."
".\.venv\Scripts\python.exe" -u gui_server.py
