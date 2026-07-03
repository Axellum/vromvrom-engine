from core.llm_gateway import LLMGateway
import logging

logger = logging.getLogger(__name__)

class ContextManager:
    """
    Composant clé pour l'optimisation des tokens (Memory Management).
    Compresse les sorties trop longues via un LLM local, avec fallback Cloud.
    """
    _instance = None
    _shared_executor = None
    
    @classmethod
    def get_instance(cls):
        return cls._instance
        
    def __init__(self, llm_gateway: LLMGateway, cloud_provider_name: str = "deepseek"):
        self.gateway = llm_gateway
        self.cloud_provider_name = cloud_provider_name
        self.threshold = 1500 # Caractères avant déclenchement de la compression
        self.semantic_cache = {} # filepath -> { "summary": str, "mtime": float, "size": int }
        ContextManager._instance = self
        self._load_ignore_patterns()
        
        # Initialisation unique et partagée du ThreadPoolExecutor pour éviter l'overhead
        # et les fuites de threads d'allocations dynamiques répétées.
        if ContextManager._shared_executor is None:
            from concurrent.futures import ThreadPoolExecutor
            ContextManager._shared_executor = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="ContextManagerPool"
            )
        self._executor = ContextManager._shared_executor
        
    def _load_ignore_patterns(self):
        """Charge les motifs d'exclusion de .antigravityignore / .claudeignore."""
        import os
        self.ignore_patterns = []
        
        # Chercher dans le dossier racine du workspace
        workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        ignore_files = [
            os.path.join(workspace_root, ".antigravityignore"),
            os.path.join(workspace_root, ".claudeignore"),
            ".antigravityignore",
            ".claudeignore"
        ]
        
        for file_path in ignore_files:
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                self.ignore_patterns.append(line)
                    logger.info(f"[CONTEXT MANAGER] Charge {len(self.ignore_patterns)} motifs depuis {file_path}")
                    break
                except Exception as e:
                    logger.warning(f"[CONTEXT MANAGER] Impossible de lire {file_path}: {e}")

    def is_ignored(self, filepath: str) -> bool:
        """Vérifie si un fichier doit être exclu du contexte d'après les motifs d'ignore."""
        import os
        force_check = getattr(self, "force_ignore_check", False)
        if os.environ.get("PYTEST_CURRENT_TEST") and not force_check:
            return False  # Pas d'ignore automatique pendant les tests unitaires
            
        if not hasattr(self, 'ignore_patterns'):
            self._load_ignore_patterns()
            
        if not self.ignore_patterns:
            return False
            
        import fnmatch
        import os
        
        basename = os.path.basename(filepath)
        normalized_path = filepath.replace("\\", "/")
        
        for pattern in self.ignore_patterns:
            pat = pattern.replace("\\", "/")
            if fnmatch.fnmatch(basename, pat) or fnmatch.fnmatch(normalized_path, f"*{pat}*"):
                return True
        return False
        
    def optimize_file_read(self, filepath: str, raw_content: str) -> str:
        """
        Optimise le contenu d'un fichier lu s'il dépasse une certaine taille.
        Gère un cache sémantique pour éviter de résumer inutilement à chaque relecture.
        """
        import os
        
        # Ignorer si spécifié dans .antigravityignore
        if self.is_ignored(filepath):
            logger.info(f"[CONTEXT MANAGER] Fichier exclu par ignore : {os.path.basename(filepath)}")
            return f"[Contenu de {os.path.basename(filepath)} ignoré par .antigravityignore]"
            
        # Seuil de compression pour les fichiers de contexte (10 Ko)
        file_size_threshold = 10000
        
        if len(raw_content) <= file_size_threshold:
            return raw_content
            
        # Ne pas compresser si ce n'est pas un fichier markdown (règles/contextes)
        if not filepath.endswith(('.md', '.markdown')):
            return raw_content
            
        try:
            mtime = os.path.getmtime(filepath)
            size = len(raw_content)
        except Exception:
            mtime = 0
            size = len(raw_content)
            
        # Vérifier si le fichier est en cache et s'il n'a pas changé
        cache_entry = self.semantic_cache.get(filepath)
        if cache_entry and cache_entry["mtime"] == mtime and cache_entry["size"] == size:
            logger.info(f"[CONTEXT MANAGER] Utilisation du résumé sémantique en cache pour : {os.path.basename(filepath)}")
            return cache_entry["summary"]
            
        print(f"\n[CONTEXT MANAGER] -> Fichier de contexte volumineux détecté ({size} chars). Résumé sémantique en cours pour : {os.path.basename(filepath)}...")
        
        system_prompt = """Tu es un expert en compression sémantique.
Ta mission est de résumer ce document de référence / règles domotiques.
CONSIGNES STRICTES :
1. Conserve toutes les informations clés : adresses IP, numéros de GPIO, valeurs de configuration, chemins absolus, limitations matérielles.
2. Structure le résumé avec des titres clairs.
3. Rends le document 3 à 5 fois plus court sans perte d'information technique structurante."""

        user_prompt = f"Contenu du fichier {os.path.basename(filepath)} :\n\n{raw_content}"
        
        try:
            # On utilise le summarize de base pour appeler le LLM local/fallback cloud
            summary = self.summarize(raw_content)
            # Si le résumé généré est valide, on le met en cache
            if summary and len(summary) < len(raw_content):
                self.semantic_cache[filepath] = {
                    "summary": summary,
                    "mtime": mtime,
                    "size": size
                }
                logger.info(f"[CONTEXT MANAGER] Résumé sémantique mis en cache pour {os.path.basename(filepath)} (Ratio: {len(summary)/len(raw_content):.2f})")
                return summary
        except Exception as e:
            logger.error(f"[CONTEXT MANAGER] Échec de la génération du résumé sémantique pour {filepath} : {e}")
            
        return raw_content

        
    def clean_raw_data(self, raw_data: str) -> str:
        """Nettoie le bruit dans les logs (lignes vides, doublons consécutifs) pour économiser des tokens."""
        if not raw_data:
            return ""
            
        lines = raw_data.splitlines()
        cleaned_lines = []
        last_line = None
        empty_count = 0
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                empty_count += 1
                if empty_count <= 1:
                    cleaned_lines.append("")
                continue
            else:
                empty_count = 0
                
            # Éliminer les doublons consécutifs exacts (fréquent dans les boucles infinies de logs)
            if stripped == last_line:
                continue
                
            cleaned_lines.append(line)
            last_line = stripped
            
        return "\n".join(cleaned_lines)

    def summarize(self, raw_data: str) -> str:
        """
        Analyse une chaîne de caractères et la résume si elle dépasse le seuil.
        
        Fix P1: Détection runtime si generate() retourne une coroutine (provider async)
        pour éviter le RuntimeWarning 'coroutine was never awaited'.
        """
        if not raw_data or len(raw_data) <= self.threshold:
            return raw_data
            
        cleaned_data = self.clean_raw_data(raw_data)
        
        # Avertissement si taille de contexte massive (>150k tokens estimé)
        if len(cleaned_data) > 600000:
            logger.warning(
                f"[CONTEXT MANAGER] ⚠️ Volume de contexte massif détecté : "
                f"{len(cleaned_data)} caractères (~{len(cleaned_data)//4} tokens). "
                f"Risque de saturation de la fenêtre de contexte LLM !"
            )
            
        print(f"\n[MEMORY] -> Résultat très long ({len(raw_data)} chars bruts, {len(cleaned_data)} nettoyés). Compression en cours...")
        system_prompt = """Tu es un compresseur de contexte pour un moteur d'orchestration IA.
Ta mission est de résumer ce log d'exécution technique. 
CONSIGNES STRICTES :
1. Conserve IMPÉRATIVEMENT les chemins de fichiers, adresses IP, URLs, statuts et messages d'erreurs.
2. Retire tout le texte de remplissage.
3. Sois ultra-concis (style télégraphique)."""

        user_prompt = f"Données brutes à résumer :\n\n{cleaned_data[:20000]}"
        
        def _safe_call(provider_method, *args, **kwargs) -> str:
            """Appelle un provider.generate() en gérant le cas où il retourne une coroutine."""
            import inspect
            result = provider_method(*args, **kwargs)
            if inspect.iscoroutine(result):
                # Fix P1 : le provider est async → on exécute dans l'event loop existant ou on en crée un
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    # On est dans un contexte asyncio → soumettre dans notre pool persistant
                    future = self._executor.submit(asyncio.run, result)
                    return future.result(timeout=60)
                except RuntimeError:
                    # Pas de loop en cours → run_until_complete
                    return asyncio.run(result)
            return result
        
        try:
            logger.info("[MEMORY] Tentative via LM Studio Local...")
            local_provider = self.gateway.get_provider("local")
            return _safe_call(local_provider.generate, system_prompt, user_prompt, max_tokens=500)
            
        except Exception as e:
            if "ConnectionError" in type(e).__name__ or "Timeout" in type(e).__name__ or "400" in str(e):
                print(f"[MEMORY] -> LM Studio injoignable/erreur. Fallback vers {self.cloud_provider_name}...")
                try:
                    cloud_provider = self.gateway.get_provider(self.cloud_provider_name)
                    return _safe_call(cloud_provider.generate, system_prompt, user_prompt)
                except Exception as e_cloud:
                    logger.error(f"[MEMORY] Échec Fallback {self.cloud_provider_name} : {e_cloud}")
                    return cleaned_data[:self.threshold] + "\n...[TRONQUÉ BRUTALEMENT]"
            else:
                logger.error(f"[MEMORY] Erreur inattendue : {e}")
                return cleaned_data[:self.threshold] + "\n...[TRONQUÉ]"

