@echo off
rem Lancement silencieux du moteur d'agents (login Windows / démarrage auto).
rem Appelé par start_moteur_silent.vbs (fenêtre masquée) — logs dans moteur_pc.log.
rem -u : sortie non bufferisée — sinon un démarrage bloqué laisse un log VIDE (indiagnosticable).
cd /d "%~dp0.."
".venv\Scripts\python.exe" -u gui_server.py --host 0.0.0.0 --port 8000 > "moteur_pc.log" 2>&1
