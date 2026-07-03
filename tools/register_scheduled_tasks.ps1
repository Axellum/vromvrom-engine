# register_scheduled_tasks.ps1
# Script pour inscrire les tâches automatisées du Moteur Agents dans Windows

$ActionCleanBrain = New-ScheduledTaskAction -Execute "python" -Argument "H:\AuxFilsDesIdees\moteur_agents\tools\clean_antigravity_brain.py"
$TriggerCleanBrain = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3:00AM
Register-ScheduledTask -Action $ActionCleanBrain -Trigger $TriggerCleanBrain -TaskName "MoteurAgents_CleanBrain" -Description "Archive les sessions de l'IDE vieilles de plus de 14 jours" -Force
Write-Host "Tâche MoteurAgents_CleanBrain enregistrée."

$ActionBatchNuit = New-ScheduledTaskAction -Execute "python" -Argument "H:\AuxFilsDesIdees\moteur_agents\tools\build_meta_analysis_batch.py"
$TriggerBatchNuit = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 2:00AM
Register-ScheduledTask -Action $ActionBatchNuit -Trigger $TriggerBatchNuit -TaskName "MoteurAgents_BatchNuit" -Description "Exécute la méta-analyse hebdomadaire (dette technique)" -Force
Write-Host "Tâche MoteurAgents_BatchNuit enregistrée."

$ActionRagSync = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-Command `"python H:\AuxFilsDesIdees\moteur_agents\tools\verify_facts_freshness.py; python H:\AuxFilsDesIdees\moteur_agents\sync_db_to_markdown.py`""
$TriggerRagSync = New-ScheduledTaskTrigger -Daily -At 4:00AM
Register-ScheduledTask -Action $ActionRagSync -Trigger $TriggerRagSync -TaskName "MoteurAgents_RAG_Freshness" -Description "Vérifie les faits et synchronise la DB vers le Markdown" -Force
Write-Host "Tâche MoteurAgents_RAG_Freshness enregistrée."

Write-Host "Toutes les tâches planifiées ont été configurées avec succès."
