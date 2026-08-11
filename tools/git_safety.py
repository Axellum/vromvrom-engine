"""
tools/git_safety.py — Garde-fou : vérifie que le dépôt Git courant pointe bien
vers le remote canonique (Axellum/moteur_agents) avant toute opération Git.

Empêche de committer/merger silencieusement dans un dépôt orphelin (ex: un
`.git` créé par erreur, sans remote, dans un dossier de travail en prod).
Utilisé par core/router.py, core/engine.py, api/routes/backlog.py.
"""
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# Remote GitHub canonique du projet. Sert de garde-fou : si un `.git` traîne
# dans le dossier de travail (ex: `git init` fait par erreur en prod, sans
# remote) sans pointer vers ce dépôt, on refuse d'opérer dessus plutôt que de
# committer/merger silencieusement dans un dépôt orphelin non lié à l'historique
# réel (cf. incident Steam Deck du 2026-06-27 : ~400 fichiers absorbés dans un
# dépôt sans remote créé par erreur dans /home/deck/dev_station/moteur_agents).
EXPECTED_REMOTE_SUBSTR = "Axellum/moteur_agents"

def _run_git(args: list, cwd: str = ".") -> tuple[int, str, str]:
    """
    Fonction utilitaire pour exécuter une commande Git de façon robuste.
    Retourne le code de retour, stdout et stderr.
    """
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        logger.error(f"Erreur d'exécution de git {' '.join(args)} : {e}")
        return -1, "", str(e)

def is_git_repo(path: str = ".") -> bool:
    """
    Vérifie si le dossier spécifié est un dépôt Git.
    """
    code, out, _ = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return code == 0 and out == "true"

def is_canonical_repo(repo_path: str = ".") -> bool:
    """
    Vérifie que le dépôt Git à repo_path est bien le dépôt canonique du projet
    (remote 'origin' pointant vers EXPECTED_REMOTE_SUBSTR), et non un dépôt
    orphelin (ex: `git init` accidentel sans remote) qui se trouverait par
    hasard au même emplacement.
    """
    code, remote_url, _ = _run_git(["remote", "get-url", "origin"], cwd=repo_path)
    return code == 0 and EXPECTED_REMOTE_SUBSTR in remote_url

def _require_canonical_repo(repo_path: str) -> str:
    """
    Retourne une chaîne d'erreur (préfixée "Erreur") si repo_path n'est pas un
    dépôt Git canonique du projet, sinon une chaîne vide.
    """
    if not is_git_repo(repo_path):
        return "Erreur : Le dossier de travail n'est pas un dépôt Git."
    if not is_canonical_repo(repo_path):
        logger.error(
            f"[GIT SAFETY] Dépôt non canonique détecté dans '{repo_path}' "
            f"(remote 'origin' absent ou différent de '{EXPECTED_REMOTE_SUBSTR}'). "
            "Opération refusée pour éviter d'agir sur un dépôt orphelin."
        )
        return (
            "Erreur : Le dépôt Git à cet emplacement n'est pas le dépôt canonique "
            f"du projet (remote 'origin' manquant ou ≠ {EXPECTED_REMOTE_SUBSTR})."
        )
    return ""

def git_create_checkpoint(repo_path: str = ".") -> str:
    """
    Crée un checkpoint Git en sauvegardant l'état actuel de l'espace de travail.
    Si des modifications non validées (tracked ou untracked) existent,
    elles sont stockées dans un stash temporaire.
    """
    guard_err = _require_canonical_repo(repo_path)
    if guard_err:
        return guard_err

    # Vérifier s'il y a des modifications en cours (fichiers modifiés ou non suivis)
    code, status_out, _ = _run_git(["status", "--porcelain"], cwd=repo_path)
    if code != 0:
        return "Erreur lors de la vérification de l'état Git."

    if not status_out:
        return "Succès : Aucun changement détecté dans l'espace de travail. Checkpoint créé (état propre)."

    # Création d'un message unique pour notre stash
    import time
    checkpoint_id = f"agent_checkpoint_{int(time.time())}"

    # stash push --include-untracked pour tout sauvegarder
    code, stdout, stderr = _run_git(
        ["stash", "push", "--include-untracked", "-m", checkpoint_id],
        cwd=repo_path
    )

    if code != 0:
        return f"Erreur lors de la création du stash Git : {stderr or stdout}"

    return f"Succès : Checkpoint Git créé avec succès. ID : {checkpoint_id}. Modifications stashed."

def git_rollback_checkpoint(repo_path: str = ".") -> str:
    """
    Restaure l'espace de travail à son état d'origine.
    Supprime toutes les modifications apportées depuis le checkpoint (git reset + git clean)
    puis applique le dernier stash s'il s'agissait de notre checkpoint d'agent.
    """
    guard_err = _require_canonical_repo(repo_path)
    if guard_err:
        return guard_err

    # 1. Annuler toutes les modifications locales (fichiers suivis)
    code, stdout, stderr = _run_git(["reset", "--hard", "HEAD"], cwd=repo_path)
    if code != 0:
        return f"Erreur lors du hard reset : {stderr or stdout}"

    # 2. Nettoyer les fichiers non suivis
    code, stdout, stderr = _run_git(["clean", "-fd"], cwd=repo_path)
    if code != 0:
        logger.warning(f"Avertissement lors du nettoyage Git (clean) : {stderr or stdout}")

    # 3. Vérifier s'il y a un stash d'agent créé par nous en haut de la pile
    code, stdout, stderr = _run_git(["stash", "list"], cwd=repo_path)
    if code == 0 and stdout:
        first_line = stdout.splitlines()[0]
        if "pre_agent_checkpoint_" in first_line or "agent_checkpoint_" in first_line:
            # Pop le stash pour restaurer l'état initial avant le checkpoint
            pop_code, pop_out, pop_err = _run_git(["stash", "pop"], cwd=repo_path)
            if pop_code != 0:
                return f"Workspace réinitialisé, mais échec de la restauration du stash : {pop_err or pop_out}"
            return "Succès : Rollback effectué. Les modifications de l'agent ont été effacées, et votre état de travail précédent a été restauré."

    return "Succès : Rollback effectué. L'espace de travail a été nettoyé et restauré."

def git_apply_checkpoint(repo_path: str = ".") -> str:
    """
    Valide le checkpoint. Si des modifications existaient avant l'exécution de l'agent,
    elles sont fusionnées à nouveau avec le travail accompli par l'agent.
    """
    guard_err = _require_canonical_repo(repo_path)
    if guard_err:
        return guard_err

    # Récupérer la liste des stashes pour voir si le dernier vient de l'agent
    code, stdout, _ = _run_git(["stash", "list"], cwd=repo_path)
    if code == 0 and stdout:
        first_line = stdout.splitlines()[0]
        if "pre_agent_checkpoint_" in first_line or "agent_checkpoint_" in first_line:
            # Appliquer (ou pop) le stash pour fusionner l'ancien travail de l'utilisateur
            pop_code, pop_out, pop_err = _run_git(["stash", "pop"], cwd=repo_path)
            if pop_code != 0:
                return f"Modifications conservées, mais conflit ou échec lors de la ré-application de votre ancien état : {pop_err or pop_out}"
            return "Succès : Checkpoint validé. Votre ancien état de travail a été fusionné avec le résultat de l'agent."

    return "Succès : Checkpoint validé sans modification à ré-appliquer."

def git_prepare_agent_branch(session_id: str, repo_path: str = ".", prefix: str = "agent/run") -> str:
    """
    Crée une branche éphémère '{prefix}_{session_id}' à partir de HEAD.
    Si des modifications non validées existent dans le workspace de l'utilisateur,
    elles sont d'abord stashed pour garder la branche propre.
    """
    guard_err = _require_canonical_repo(repo_path)
    if guard_err:
        return guard_err

    # 1. Sauvegarder l'état actuel de l'utilisateur s'il y a des modifications
    code, status_out, _ = _run_git(["status", "--porcelain"], cwd=repo_path)
    user_stash_created = False
    if status_out:
        logger.info("[GIT SAFETY] Modifications locales détectées. Création d'un stash de sécurité utilisateur.")
        import time
        stash_name = f"user_pre_agent_{session_id}_{int(time.time())}"
        code, stdout, stderr = _run_git(["stash", "push", "--include-untracked", "-m", stash_name], cwd=repo_path)
        if code == 0:
            user_stash_created = True
        else:
            logger.error(f"[GIT SAFETY] Échec du stash des modifs utilisateur : {stderr or stdout}")

    # 2. Récupérer la branche d'origine actuelle
    code, orig_branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    if code != 0 or not orig_branch:
        orig_branch = "main"

    # 3. Créer et basculer sur la branche éphémère de l'agent
    import time
    if prefix.endswith("/"):
        branch_name = f"{prefix}{session_id}_{int(time.time())}"
    else:
        branch_name = f"{prefix}_{session_id}_{int(time.time())}"

    logger.info(f"[GIT SAFETY] Création de la branche de travail éphémère : {branch_name} depuis {orig_branch}")
    code, stdout, err = _run_git(["checkout", "-b", branch_name], cwd=repo_path)
    if code != 0:
        logger.error(f"[GIT SAFETY] Impossible de créer la branche Git éphémère : {err or stdout}")
        # Restauration du stash s'il a été créé
        if user_stash_created:
            _run_git(["stash", "pop"], cwd=repo_path)
        return f"Erreur lors de la création de la branche : {err or stdout}"

    return branch_name

def git_generate_semantic_commit_msg(repo_path: str = ".", session_id: str = "") -> str:
    """
    Génère un message de commit sémantique basé sur le diff Git courant.
    Analyse les fichiers modifiés et produit un message au format Conventional Commits
    (feat:, fix:, refactor:, docs:, style:, chore:, etc.)

    Le message est généré sans appel LLM (analyse heuristique locale) pour éviter
    les coûts en tokens. Le LLM peut enrichir le message ultérieurement.
    """
    # 1. Récupérer la liste des fichiers modifiés avec statistiques
    code, stat_out, _ = _run_git(["diff", "--staged", "--stat"], cwd=repo_path)
    if code != 0 or not stat_out:
        # Fallback : fichiers non staged
        code, stat_out, _ = _run_git(["diff", "--stat"], cwd=repo_path)

    if not stat_out:
        return f"chore(agent): session {session_id} — aucune modification détectée"

    # 2. Récupérer la liste des fichiers modifiés (noms uniquement)
    code, names_out, _ = _run_git(["diff", "--staged", "--name-only"], cwd=repo_path)
    if code != 0 or not names_out:
        code, names_out, _ = _run_git(["diff", "--name-only"], cwd=repo_path)

    modified_files = [f.strip() for f in names_out.splitlines() if f.strip()] if names_out else []

    # 3. Analyse heuristique du type de commit
    commit_type = "feat"  # Par défaut
    scope = "agent"

    # Détection du scope basée sur les chemins de fichiers
    scope_map = {
        "core/": "core",
        "agents/": "agents",
        "tools/": "tools",
        "static/": "ui",
        "docs/": "docs",
        "config": "config",
        "test": "test",
    }

    detected_scopes = set()
    for mf in modified_files:
        for prefix, s in scope_map.items():
            if prefix in mf.lower():
                detected_scopes.add(s)
                break

    if detected_scopes:
        scope = ",".join(sorted(detected_scopes))

    # Détection du type de commit basée sur les noms de fichiers et le diff
    if any("test" in f.lower() for f in modified_files):
        commit_type = "test"
    elif any(f.endswith((".md", ".txt", ".rst")) for f in modified_files):
        if all(f.endswith((".md", ".txt", ".rst")) for f in modified_files):
            commit_type = "docs"
    elif any("fix" in f.lower() or "patch" in f.lower() or "bug" in f.lower() for f in modified_files):
        commit_type = "fix"
    elif any(".css" in f or ".html" in f for f in modified_files):
        if all(".css" in f or ".html" in f for f in modified_files):
            commit_type = "style"
    elif any("config" in f.lower() for f in modified_files):
        if all("config" in f.lower() or f.endswith(".json") for f in modified_files):
            commit_type = "chore"

    # 4. Extraction d'un résumé concis des fichiers principaux
    file_summary = ", ".join(os.path.basename(f) for f in modified_files[:5])
    if len(modified_files) > 5:
        file_summary += f" (+{len(modified_files) - 5} fichiers)"

    # 5. Lecture du nombre de lignes ajoutées/supprimées depuis --stat
    stat_lines = stat_out.strip().splitlines()
    stat_summary = stat_lines[-1] if stat_lines else ""

    # 6. Construction du message de commit
    commit_msg = f"{commit_type}({scope}): {file_summary}"

    # Corps du message (limité à 200 chars)
    body = f"Session: {session_id}\n{stat_summary}"
    if len(body) > 200:
        body = body[:200]

    full_msg = f"{commit_msg}\n\n{body}"
    logger.info(f"[GIT SAFETY] Message de commit sémantique généré : {commit_msg}")

    return full_msg

def git_finalize_agent_branch(branch_name: str, success: bool, session_id: str, repo_path: str = ".") -> str:
    """
    Finalise le travail de l'agent.
    - Si SUCCÈS : merge la branche éphémère vers sa branche d'origine (main).
    - Si ÉCHEC : détruit la branche éphémère (rollback).
    Dans tous les cas, restaure le stash utilisateur s'il existe.
    """
    guard_err = _require_canonical_repo(repo_path)
    if guard_err:
        return guard_err

    # 1. Déterminer la branche parente (souvent main ou master, ou lue dans la config)
    # Pour faire simple et robuste, on va tenter de merge dans 'main' ou la branche par défaut
    # Mais d'abord, on doit commiter les modifications de l'agent sur sa propre branche éphémère !
    if success:
        # Ajout et commit des modifications de l'agent sur sa branche éphémère
        code, status_out, _ = _run_git(["status", "--porcelain"], cwd=repo_path)
        if status_out:
            logger.info("[GIT SAFETY] Enregistrement des modifications de l'agent sur la branche éphémère...")
            _run_git(["add", "."], cwd=repo_path)
            # Message de commit sémantique auto-généré
            commit_msg = git_generate_semantic_commit_msg(repo_path, session_id)
            _run_git(["commit", "-m", commit_msg], cwd=repo_path)

    # 2. Revenir sur 'main'
    target_branch = "main"
    logger.info(f"[GIT SAFETY] Retour sur la branche principale '{target_branch}'")
    code, stdout, err = _run_git(["checkout", target_branch], cwd=repo_path)
    if code != 0:
        # Si 'main' n'existe pas ou erreur, tenter 'master'
        target_branch = "master"
        code, stdout, err = _run_git(["checkout", target_branch], cwd=repo_path)
        if code != 0:
            return f"Erreur lors du retour à la branche principale : {err or stdout}"

    merge_status = ""
    if success:
        logger.info(f"[GIT SAFETY] Fusion de la branche éphémère '{branch_name}' dans '{target_branch}'...")
        code, out, err = _run_git(["merge", branch_name, "--no-ff", "-m", f"Merge agent run {session_id}"], cwd=repo_path)
        if code != 0:
            logger.warning(f"[GIT SAFETY] Conflits lors du merge ! Le travail de l'agent reste isolé dans '{branch_name}'.")
            merge_status = f"Conflits de fusion. Le travail de l'agent est conservé dans la branche '{branch_name}' pour résolution manuelle."
            # Annuler le merge en cours pour garder 'main' propre
            _run_git(["merge", "--abort"], cwd=repo_path)
        else:
            merge_status = "Fusion effectuée avec succès."
            # Supprimer la branche locale éphémère
            _run_git(["branch", "-d", branch_name], cwd=repo_path)
    else:
        logger.warning(f"[GIT SAFETY] Échec détecté. Annulation des changements et abandon de la branche '{branch_name}'.")
        # Annuler toutes les modifications locales sur la branche éphémère avant de la quitter
        _run_git(["reset", "--hard", "HEAD"], cwd=repo_path)
        _run_git(["clean", "-fd"], cwd=repo_path)
        # Forcer le retour et la suppression de la branche
        _run_git(["checkout", target_branch], cwd=repo_path)
        _run_git(["branch", "-D", branch_name], cwd=repo_path)
        merge_status = "Rollback effectué (branche éphémère détruite)."


    # 3. Restaurer le stash utilisateur s'il existe
    code, stdout, _ = _run_git(["stash", "list"], cwd=repo_path)
    if code == 0 and stdout:
        lines = stdout.splitlines()
        for idx, line in enumerate(lines):
            if f"user_pre_agent_{session_id}" in line:
                logger.info(f"[GIT SAFETY] Restauration du stash utilisateur (index {idx})...")
                pop_code, pop_out, pop_err = _run_git(["stash", "pop", f"stash@{{{idx}}}"], cwd=repo_path)
                if pop_code != 0:
                    # [#T278] Un `stash pop` en échec laisse le stash INTACT dans la
                    # pile (git ne le dépile que si l'application réussit). Avant ce
                    # correctif, une simple phrase ajoutée au message de succès
                    # (« Fusion effectuée avec succès. Attention : conflit… ») laissait
                    # croire que tout allait bien — la config de prod pouvait rester
                    # enfermée dans un stash jamais dépilé (incident du 11/08).
                    # Le statut retourné porte donc l'échec et nomme l'identifiant
                    # exact du stash resté intact pour une récupération manuelle.
                    logger.error(
                        f"[GIT SAFETY] Échec de la restauration du stash utilisateur "
                        f"(stash@{{{idx}}}, laissé intact) : {pop_err or pop_out}"
                    )
                    merge_status = (
                        f"ÉCHEC de la restauration de vos modifications locales : le "
                        f"stash stash@{{{idx}}} est resté intact dans la pile. "
                        f"Récupération manuelle : git stash apply stash@{{{idx}}}"
                    )
                else:
                    merge_status += " Vos modifications locales ont été restaurées."
                break

    return merge_status


def lister_stash_agents(repo_path: str = ".") -> list[dict]:
    """
    Diagnostic [#T278] : liste les stash créés par l'agent (préfixe
    'user_pre_agent_') encore présents dans la pile, avec leur identifiant
    (stash@{N}), leur message et leur âge. Sert à constater l'accumulation de
    stash jamais dépilés après des `pop` en échec. Ne supprime RIEN : purger un
    stash est irréversible et relève d'une décision humaine.
    """
    stashes: list[dict] = []
    code, stdout, _ = _run_git(
        ["stash", "list", "--format=%gd%x09%gs%x09%cr"],
        cwd=repo_path,
    )
    if code != 0 or not stdout:
        return stashes
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        stash_id, message, age = parts[0], parts[1], parts[2]
        if "user_pre_agent_" in message:
            stashes.append({"id": stash_id, "message": message, "age": age})
    return stashes
