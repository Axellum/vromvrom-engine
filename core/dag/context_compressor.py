"""
core/dag/context_compressor.py — Utilitaires de compression de contexte et d'analyse de code pour le DAG.
"""

import logging
import re

logger = logging.getLogger(__name__)


class ContextCompressor:
    """
    Compresse le résultat des tâches parents pour éviter de saturer le contexte LLM des tâches enfants.
    """

    # Seuil en caractères au-delà duquel le contexte d'une dépendance est compressé.
    # Calibré pour que le total (système + objectif + contexte) reste sous ~50K tokens
    # même avec plusieurs dépendances. 15K chars ≈ 4K tokens.
    CONTEXT_COMPRESSION_THRESHOLD = 15_000

    # Budget max par dépendance après compression (en chars)
    COMPRESSED_MAX_CHARS = 12_000

    def __init__(self, context_manager=None):
        """
        Args:
            context_manager: Le gestionnaire de contexte du moteur (optionnel, pour résumé LLM).
        """
        self.context_manager = context_manager

    def compress_context(self, raw_data: str, dep_id: str) -> str:
        """
        Compresse le résultat d'une tâche parent avant injection comme contexte.

        Stratégie hybride à 3 niveaux :
        1. Si < seuil → retour tel quel
        2. Compression structurelle : extraction des fichiers/fonctions/structure
        3. Fallback : troncature intelligente (début + fin)

        Args:
            raw_data: Le résultat brut de la tâche parent
            dep_id: L'identifiant de la tâche parent (pour les logs)

        Returns:
            Le contexte compressé, garanti sous COMPRESSED_MAX_CHARS caractères
        """
        if len(raw_data) <= self.CONTEXT_COMPRESSION_THRESHOLD:
            return raw_data

        original_len = len(raw_data)
        logger.info(
            f"[DAG] 🗜️ Compression du contexte de '{dep_id}' : "
            f"{original_len:,} chars → max {self.COMPRESSED_MAX_CHARS:,} chars"
        )

        # ── Niveau 1 : Compression structurelle pour du code source ──
        # Détecte si le contenu est du code source (YAML, C++, Python)
        # et extrait uniquement la structure (noms de fichiers, signatures, headers)
        if self._looks_like_code_dump(raw_data):
            compressed = self._extract_code_structure(raw_data)
            if compressed and len(compressed) <= self.COMPRESSED_MAX_CHARS:
                logger.info(
                    f"[DAG] ✅ Compression structurelle : "
                    f"{original_len:,} → {len(compressed):,} chars "
                    f"(ratio: {len(compressed)/original_len:.2%})"
                )
                return compressed

        # ── Niveau 2 : Résumé LLM via context_manager ──
        if self.context_manager:
            try:
                summarized = self.context_manager.summarize(raw_data)
                if summarized and len(summarized) < original_len:
                    # Tronquer le résumé LLM s'il dépasse encore le budget
                    if len(summarized) > self.COMPRESSED_MAX_CHARS:
                        summarized = summarized[:self.COMPRESSED_MAX_CHARS - 100]
                        summarized += "\n\n[... résumé LLM tronqué ...]"
                    logger.info(
                        f"[DAG] ✅ Résumé LLM : "
                        f"{original_len:,} → {len(summarized):,} chars"
                    )
                    return summarized
            except Exception as e:
                logger.warning(f"[DAG] Résumé LLM échoué pour '{dep_id}' : {e}")

        # ── Niveau 3 : Troncature intelligente (début + fin) ──
        return self._smart_truncate(raw_data, dep_id)

    def _looks_like_code_dump(self, text: str) -> bool:
        """
        Détecte si le texte ressemble à un dump de code source multi-fichiers.
        Heuristique basée sur la présence de marqueurs de fichiers et de syntaxe.
        """
        # Indicateurs de dump de code source
        indicators = 0
        sample = text[:5000]  # Échantillonner le début

        # Marqueurs de noms de fichiers
        file_patterns = re.findall(
            r'(?:^|\n)(?:---|===|###|//|#)\s*(?:Fichier|File|---)\s*[:>]?\s*\S+\.(?:yaml|yml|cpp|h|py)',
            sample, re.IGNORECASE
        )
        if file_patterns:
            indicators += 2

        # Extensions de fichiers dans le texte
        extensions = re.findall(r'\.\w{1,4}(?:\s|$|:|\))', sample)
        code_ext = [e for e in extensions if any(
            ext in e.lower() for ext in ['.yaml', '.yml', '.cpp', '.h', '.py', '.json']
        )]
        if len(code_ext) >= 3:
            indicators += 1

        # Indentation significative (code)
        indented_lines = len(re.findall(r'\n {2,}\S', sample))
        if indented_lines > 10:
            indicators += 1

        # Mots-clés YAML/C++/Python
        code_kw = ['esphome:', 'substitutions:', '#include', 'def ', 'class ',
                    'void ', 'lambda:', 'sensor:', 'display:', 'lvgl:']
        kw_count = sum(1 for kw in code_kw if kw in sample)
        if kw_count >= 2:
            indicators += 2

        return indicators >= 3

    def _extract_code_structure(self, raw_data: str) -> str:
        """
        Extrait la structure d'un dump de code source multi-fichiers :
        - Noms des fichiers et leurs tailles
        - Sections/headers principaux (esphome:, sensor:, display:, etc.)
        - Premières lignes de chaque bloc (contexte suffisant pour l'audit)
        - Signatures de fonctions C++ / Python

        Produit un résumé structurel concis utilisable par un auditeur.
        """
        lines = raw_data.split('\n')
        total_lines = len(lines)

        # Détecter les blocs de fichiers (séparés par des headers)
        file_blocks = []
        current_file = None
        current_content = []

        for line in lines:
            # Patterns de headers de fichiers
            file_match = re.match(
                r'^(?:---|===|###|#{1,3})\s*(?:Fichier|File|Contenu de)\s*[:>]?\s*(.+?)(?:\s*---|$)',
                line, re.IGNORECASE
            )
            if not file_match:
                file_match = re.match(
                    r'^(?:---\s+)?(\S+\.(?:yaml|yml|cpp|h|py|json|md))\s*(?:---)?$',
                    line.strip()
                )
            if not file_match:
                # Pattern: "## nom_fichier.yaml" ou "### path/to/file.cpp"
                file_match = re.match(
                    r'^#{1,4}\s+(?:.*?)(\S+\.(?:yaml|yml|cpp|h|py|json))',
                    line
                )

            if file_match:
                # Sauver le bloc précédent
                if current_file:
                    file_blocks.append((current_file, current_content))
                current_file = file_match.group(1).strip()
                current_content = []
            else:
                current_content.append(line)

        # Dernier bloc
        if current_file:
            file_blocks.append((current_file, current_content))

        # Si pas de blocs de fichiers détectés, fallback sur troncature
        if not file_blocks:
            # Essayer une extraction par sections YAML (clés racine)
            return self._extract_yaml_structure(raw_data)

        # Construire le résumé structurel
        result_parts = [
            f"## 📁 Structure du code source ({total_lines:,} lignes, {len(raw_data):,} chars)",
            f"### Fichiers détectés : {len(file_blocks)}",
            ""
        ]

        budget_per_file = max(
            500,  # Minimum 500 chars par fichier
            (self.COMPRESSED_MAX_CHARS - 500) // max(1, len(file_blocks))
        )

        for filename, content in file_blocks:
            content_text = '\n'.join(content)
            content_lines = len(content)
            content_chars = len(content_text)

            result_parts.append(f"### 📄 {filename} ({content_lines} lignes, {content_chars:,} chars)")

            # Extraire les éléments structurels clés
            key_elements = self._extract_key_elements(content, filename)
            if key_elements:
                result_parts.append(key_elements)

            # Inclure un extrait du contenu (début)
            excerpt = content_text[:budget_per_file]
            if len(content_text) > budget_per_file:
                # Trouver la fin de la dernière ligne complète
                last_newline = excerpt.rfind('\n')
                if last_newline > budget_per_file // 2:
                    excerpt = excerpt[:last_newline]
                excerpt += f"\n[... {content_lines - excerpt.count(chr(10))} lignes restantes omises ...]"

            result_parts.append(f"```\n{excerpt}\n```")
            result_parts.append("")

        result = '\n'.join(result_parts)

        # Garde-fou : tronquer si le résultat dépasse le budget
        if len(result) > self.COMPRESSED_MAX_CHARS:
            result = result[:self.COMPRESSED_MAX_CHARS - 200]
            result += f"\n\n[... compression structurelle tronquée — {len(file_blocks)} fichiers au total ...]"

        return result

    def _extract_key_elements(self, content_lines: list, filename: str) -> str:
        """
        Extrait les éléments clés d'un fichier selon son type :
        - YAML : clés racine (esphome:, sensor:, display:, etc.)
        - C++/H : signatures de fonctions, #include, classes
        - Python : classes et fonctions def
        """
        elements = []
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

        if ext in ('yaml', 'yml'):
            # Extraire les clés racine YAML (non indentées)
            root_keys = []
            for line in content_lines:
                if line and not line.startswith(' ') and not line.startswith('#'):
                    key = line.split(':')[0].strip()
                    if key and not key.startswith('-'):
                        root_keys.append(key)
            if root_keys:
                elements.append(f"**Sections YAML** : {', '.join(root_keys[:20])}")

        elif ext in ('cpp', 'c', 'h', 'hpp'):
            # Extraire les signatures de fonctions et #include
            includes = []
            functions = []
            for line in content_lines:
                stripped = line.strip()
                if stripped.startswith('#include'):
                    includes.append(stripped)
                # Signatures de fonctions (heuristique)
                if re.match(r'^(?:void|int|float|bool|auto|static|inline|extern)\s+\w+\s*\(', stripped):
                    functions.append(stripped.split('{')[0].strip())
            if includes:
                elements.append(f"**Includes** : {', '.join(includes[:10])}")
            if functions:
                elements.append(f"**Fonctions** : {', '.join(functions[:10])}")

        elif ext == 'py':
            # Extraire les classes et fonctions Python
            classes = []
            funcs = []
            for line in content_lines:
                stripped = line.strip()
                if stripped.startswith('class '):
                    classes.append(stripped.split('(')[0].replace('class ', ''))
                elif stripped.startswith('def '):
                    funcs.append(stripped.split('(')[0].replace('def ', ''))
            if classes:
                elements.append(f"**Classes** : {', '.join(classes[:10])}")
            if funcs:
                elements.append(f"**Fonctions** : {', '.join(funcs[:15])}")

        return '\n'.join(elements) if elements else ''

    def _extract_yaml_structure(self, raw_data: str) -> str:
        """
        Fallback : extraire la structure de haut niveau d'un contenu YAML/texte
        quand on ne peut pas identifier des blocs de fichiers individuels.
        """
        lines = raw_data.split('\n')

        # Extraire les clés racine et les sections principales
        sections = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Clé YAML racine (non indentée)
            if stripped and not stripped.startswith(' ') and ':' in stripped and not stripped.startswith('#'):
                key = stripped.split(':')[0]
                sections.append(f"L{i+1}: {key}")
            # Headers markdown
            elif stripped.startswith('#'):
                sections.append(f"L{i+1}: {stripped[:80]}")

        if not sections:
            return self._smart_truncate(raw_data, "yaml_structure")

        header = (
            f"## Structure extraite ({len(lines):,} lignes, {len(raw_data):,} chars)\n"
            f"### Sections/clés racine : {len(sections)}\n\n"
        )
        structure = '\n'.join(sections[:100])  # Max 100 sections

        # Ajouter le début du contenu brut comme échantillon
        sample_budget = self.COMPRESSED_MAX_CHARS - len(header) - len(structure) - 200
        sample = raw_data[:max(2000, sample_budget)]

        result = f"{header}{structure}\n\n### Extrait du contenu :\n```\n{sample}\n```"

        if len(result) > self.COMPRESSED_MAX_CHARS:
            result = result[:self.COMPRESSED_MAX_CHARS - 100]
            result += "\n[... tronqué ...]"

        return result

    def _smart_truncate(self, raw_data: str, dep_id: str) -> str:
        """
        Troncature intelligente : conserve le début et la fin du texte
        avec un indicateur de ce qui a été omis.
        """
        budget = self.COMPRESSED_MAX_CHARS
        head_budget = int(budget * 0.7)  # 70% pour le début
        tail_budget = int(budget * 0.2)  # 20% pour la fin
        # 10% réservé pour le message d'omission

        head = raw_data[:head_budget]
        tail = raw_data[-tail_budget:] if tail_budget > 0 else ""

        # Tronquer aux limites de lignes
        head_end = head.rfind('\n')
        if head_end > head_budget // 2:
            head = head[:head_end]

        tail_start = tail.find('\n')
        if tail_start > 0 and tail_start < tail_budget // 2:
            tail = tail[tail_start + 1:]

        omitted_chars = len(raw_data) - len(head) - len(tail)
        omitted_lines = raw_data[len(head):len(raw_data) - len(tail)].count('\n')

        separator = (
            f"\n\n[... 🗜️ {omitted_chars:,} chars / {omitted_lines:,} lignes omis "
            f"(source: '{dep_id}') ...]\n\n"
        )

        result = head + separator + tail
        logger.info(
            f"[DAG] ✅ Troncature intelligente de '{dep_id}' : "
            f"{len(raw_data):,} → {len(result):,} chars"
        )
        return result
