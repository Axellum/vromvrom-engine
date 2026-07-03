"""
core/ml_router.py — Classifieur ML pour le routing des requêtes.

Remplace le slow-path LLM par une LogisticRegression sklearn
entraînée sur l'historique de sessions (routing_type connu).

Workflow :
1. DreamerAgent appelle train() chaque nuit si >= 50 nouvelles sessions
2. predict() est consulté par Router.analyze_request() avant le slow-path LLM
3. Si confidence < 0.75 : fallback vers le router LLM standard

Auteur : Antigravity IDE + Axel
Date : 2026-06-06
"""

import asyncio
import hashlib
import json
import logging
import os
import pickle
import time
from collections import Counter
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any

import aiohttp
import numpy as np

logger = logging.getLogger(__name__)

# Chemins relatifs au répertoire moteur
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_PATH = MODELS_DIR / "ml_router.pkl"
META_PATH  = MODELS_DIR / "ml_router_meta.json"

# Configuration d'embeddings cascade (Steam Deck Ollama -> LM Studio PC -> Gemini API -> TF-IDF)
EMBEDDING_TIMEOUT = 5  # secondes maximum pour la réponse globale
CONFIDENCE_THRESHOLD = 0.75

# Classes de routing reconnues par le moteur
ROUTING_CLASSES = ["home_assistant", "casual_chat", "analysis", "code_generation", "database", "files", "sysadmin"]


def _prompt_hash(prompt: str) -> str:
    """Hash d'un prompt — DOIT rester identique à routing_metrics.record_routing_decision."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def build_training_pairs(
    sessions: List[Dict[str, Any]],
    hash_to_category: Dict[str, str],
    min_per_class: int = 2,
) -> Tuple[List[str], List[str]]:
    """
    Construit le jeu (textes, labels) pour l'entraînement du ML Router.

    Le LABEL (`dominant_category`) vit dans `routing_decisions`, le TEXTE
    (`objective`) dans `sessions`. Les deux ne sont PAS reliables par `session_id`
    (vide dans routing_decisions) : on les relie par le hash sha256(prompt)[:16],
    identique des deux côtés. Cette approche est auto-suffisante et pérenne
    (les données s'accumulent naturellement, sans changement de schéma).

    - Ne garde que les sessions réussies, objectif > 5 caractères, label connu.
    - Retire les classes ayant < `min_per_class` échantillons (sinon le split
      stratifié et l'apprentissage LogisticRegression échouent).
    """
    pairs: List[Tuple[str, str]] = []
    for s in sessions:
        if s.get("status") != "success":
            continue
        objective = (s.get("objective") or "").strip()
        if len(objective) <= 5:
            continue
        category = hash_to_category.get(_prompt_hash(objective))
        if category in ROUTING_CLASSES:
            pairs.append((objective, category))

    counts = Counter(c for _, c in pairs)
    pairs = [(t, c) for (t, c) in pairs if counts[c] >= min_per_class]
    texts = [t for t, _ in pairs]
    labels = [c for _, c in pairs]
    return texts, labels

# Singleton
_ml_router_instance: Optional["MLRouter"] = None


def _get_models_dir() -> Path:
    """Retourne le chemin absolu du dossier models, le crée si nécessaire."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return MODELS_DIR


class MLRouter:
    """
    Classifieur ML pour router les requêtes utilisateur.

    Utilise LogisticRegression scikit-learn entraîné sur l'historique
    de sessions. Vecteur d'entrée = embeddings LM Studio, Ollama (Deck), Gemini ou TF-IDF (fallback).
    """

    def __init__(self):
        """Initialise le routeur ML, charge le modèle s'il existe."""
        self._model         = None
        self._vectorizer    = None   # TF-IDF fallback
        self._use_embeddings = False
        self._classes       = []
        self._accuracy      = 0.0
        self._trained_samples = 0
        self._training_time = 0.0
        self._is_fitted     = False

        # Attributs de la cascade d'embeddings active
        self._detected_provider = None  # 'ollama', 'lmstudio', 'gemini', 'tfidf'
        self._detected_url = None
        self._detected_model = None
        self._detected_key = None

        # [#T12] Identité de l'espace d'embedding figée À L'ENTRAÎNEMENT.
        # Le provider est re-détecté à l'inférence et peut différer ; deux modèles
        # distincts peuvent partager la même dimension (nomic-embed-text et
        # text-embedding-004 = 768d). Sans ces repères, predict() classerait des
        # vecteurs d'un espace étranger → erreurs silencieuses sur le hot-path.
        self._embedding_provider = None  # provider utilisé à l'entraînement
        self._embedding_model = None     # modèle d'embedding utilisé à l'entraînement
        self._embedding_dim = None       # dimension des vecteurs d'entraînement

        # Chargement du modèle existant si déjà sauvegardé
        self._load_model()

    # ──────────────────────────────────────────────────────────────────
    # Persistance
    # ──────────────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        """Charge le modèle pickle et les métadonnées si présents."""
        model_path = _get_models_dir() / MODEL_PATH.name
        if model_path.exists():
            try:
                with open(model_path, "rb") as f:
                    data = pickle.load(f)
                self._model           = data.get("model")
                self._vectorizer      = data.get("vectorizer")
                self._use_embeddings  = data.get("use_embeddings", False)
                self._classes         = data.get("classes", [])
                self._accuracy        = data.get("accuracy", 0.0)
                self._trained_samples = data.get("trained_samples", 0)
                self._training_time   = data.get("training_time", 0.0)
                # [#T12] Identité d'embedding (absente des pickles legacy → None).
                self._embedding_provider = data.get("embedding_provider")
                self._embedding_model    = data.get("embedding_model")
                self._embedding_dim      = data.get("embedding_dim")
                self._is_fitted       = True
                if self._use_embeddings and self._embedding_model is None:
                    logger.warning(
                        "[ML ROUTER] Modèle legacy sans identité d'embedding : "
                        "garde-fou réduit à la dimension (via n_features_in_) jusqu'au "
                        "prochain réentraînement."
                    )
                logger.info(
                    f"[ML ROUTER] Modèle chargé (accuracy={self._accuracy:.3f}, "
                    f"samples={self._trained_samples}, vect={'emb' if self._use_embeddings else 'tfidf'})"
                )
            except Exception as e:
                logger.warning(f"[ML ROUTER] Erreur chargement modèle : {e}")
                self._reset()

    def _save_model(self) -> None:
        """Sauvegarde le modèle et les métadonnées en pickle/JSON."""
        models_dir = _get_models_dir()
        data = {
            "model":           self._model,
            "vectorizer":      self._vectorizer,
            "use_embeddings":  self._use_embeddings,
            "classes":         self._classes,
            "accuracy":        self._accuracy,
            "trained_samples": self._trained_samples,
            "training_time":   self._training_time,
            # [#T12] Identité de l'espace d'embedding (validée à l'inférence).
            "embedding_provider": self._embedding_provider,
            "embedding_model":    self._embedding_model,
            "embedding_dim":      self._embedding_dim,
        }
        with open(models_dir / MODEL_PATH.name, "wb") as f:
            pickle.dump(data, f)

        meta = {
            "model":           "LogisticRegression",
            "vectorizer":      (self._embedding_model or "nomic-embed-text") if self._use_embeddings else "TF-IDF",
            "embedding_provider": self._embedding_provider,
            "embedding_model":    self._embedding_model,
            "embedding_dim":      self._embedding_dim,
            "classes":         self._classes,
            "accuracy":        self._accuracy,
            "trained_samples": self._trained_samples,
            "training_time":   self._training_time,
            "threshold":       CONFIDENCE_THRESHOLD,
            "saved_at":        time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(models_dir / META_PATH.name, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        logger.info(f"[ML ROUTER] Modèle sauvegardé dans {models_dir / MODEL_PATH.name}")

    def _reset(self) -> None:
        """Réinitialise l'état du modèle."""
        self._model = self._vectorizer = None
        self._use_embeddings = self._is_fitted = False
        self._classes = []
        self._accuracy = self._trained_samples = self._training_time = 0.0
        self._embedding_provider = self._embedding_model = self._embedding_dim = None

    # ──────────────────────────────────────────────────────────────────
    # Propriétés
    # ──────────────────────────────────────────────────────────────────

    @property
    def is_trained(self) -> bool:
        """Retourne True si le modèle est entraîné et prêt."""
        return self._is_fitted and self._model is not None

    def get_stats(self) -> dict:
        """Retourne les métadonnées du modèle actuel."""
        if not self.is_trained:
            return {"trained": False}
        return {
            "trained":        True,
            "classes":        self._classes,
            "accuracy":       float(self._accuracy),
            "samples":        self._trained_samples,
            "training_time":  round(self._training_time, 3),
            "use_embeddings": self._use_embeddings,
            "threshold":      CONFIDENCE_THRESHOLD,
        }

    # ──────────────────────────────────────────────────────────────────
    # Entraînement
    # ──────────────────────────────────────────────────────────────────

    async def train(self, min_samples: int = 50) -> dict:
        """
        Entraîne le classifieur sur l'historique des sessions.

        Ordre de préférence :
        1. Embeddings LM Studio (qualité supérieure)
        2. TF-IDF (fallback si LM Studio offline)

        Returns:
            dict avec {samples, accuracy, classes, training_time} ou {error}
        """
        logger.info(f"[ML ROUTER] Début entraînement (min_samples={min_samples})")

        # Import sklearn (optionnel)
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.linear_model import LogisticRegression
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import accuracy_score
        except ImportError:
            logger.error("[ML ROUTER] scikit-learn non disponible")
            return {"error": "scikit-learn non installé (pip install scikit-learn)"}

        # Récupération des données d'entraînement.
        # Texte = sessions.objective ; label = routing_decisions.dominant_category ;
        # reliés par le hash du prompt (voir build_training_pairs).
        try:
            from core.session_history import get_sessions
            from core.runtime_db import get_connection
            sessions = await asyncio.to_thread(get_sessions, limit=2000)
        except ImportError:
            logger.error("[ML ROUTER] module session_history introuvable")
            return {"error": "session_history manquant"}

        def _load_hash_to_category() -> Dict[str, str]:
            conn = get_connection()
            rows = conn.execute(
                "SELECT user_prompt_hash, dominant_category FROM routing_decisions "
                "WHERE dominant_category IS NOT NULL AND user_prompt_hash IS NOT NULL "
                "ORDER BY timestamp ASC"
            ).fetchall()
            # En cas de hash répété, la dernière catégorie connue gagne (ORDER BY ASC).
            return {h: cat for h, cat in rows}

        try:
            hash_to_category = await asyncio.to_thread(_load_hash_to_category)
        except Exception as e:
            logger.error(f"[ML ROUTER] Lecture routing_decisions échouée : {e}")
            return {"error": f"routing_decisions illisible : {e}"}

        texts, labels = build_training_pairs(sessions, hash_to_category)

        if len(texts) < min_samples:
            logger.warning(
                f"[ML ROUTER] Pas assez d'échantillons : {len(texts)} / {min_samples}"
            )
            return {"error": f"Échantillons insuffisants ({len(texts)}/{min_samples})"}

        if len(set(labels)) < 2:
            logger.warning(f"[ML ROUTER] Une seule classe présente ({set(labels)}) — entraînement impossible.")
            return {"error": f"Diversité insuffisante : 1 seule classe ({set(labels)})"}

        logger.info(f"[ML ROUTER] {len(texts)} échantillons valides, classes={sorted(set(labels))}")

        # Tentative d'embedding via la cascade de services
        X = None
        embeddings = await self._batch_embed(texts)
        if embeddings and len(embeddings) == len(texts):
            self._use_embeddings = True
            X = np.array(embeddings)
            # [#T12] Fige l'identité de l'espace d'embedding pour la valider à l'inférence.
            self._embedding_provider = self._detected_provider
            self._embedding_model = self._detected_model
            self._embedding_dim = int(X.shape[1])
            logger.info(f"[ML ROUTER] Embeddings {self._detected_provider}/{self._detected_model} (dim={X.shape[1]})")

        # Fallback TF-IDF
        if X is None:
            self._use_embeddings = False
            self._embedding_provider = self._embedding_model = self._embedding_dim = None
            vectorizer = TfidfVectorizer(
                max_features=500, ngram_range=(1, 2), min_df=2
            )
            X = vectorizer.fit_transform(texts)
            self._vectorizer = vectorizer
            logger.info(f"[ML ROUTER] TF-IDF fallback (shape={X.shape})")

        # Encodage des labels
        self._classes = sorted(set(labels))
        label_map = {c: i for i, c in enumerate(self._classes)}
        y = np.array([label_map[l] for l in labels])

        # Split 80/20
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        # Entraînement LogisticRegression (multinomial = défaut avec solver lbfgs ;
        # le paramètre multi_class est déprécié/supprimé depuis scikit-learn 1.7).
        t0 = time.time()
        model = LogisticRegression(max_iter=500, C=1.0, random_state=42)
        await asyncio.to_thread(model.fit, X_train, y_train)
        self._training_time   = time.time() - t0
        self._model           = model
        self._trained_samples = len(texts)
        self._accuracy        = float(accuracy_score(y_test, model.predict(X_test)))
        self._is_fitted       = True

        self._save_model()

        logger.info(
            f"[ML ROUTER] ✅ Entraîné : accuracy={self._accuracy:.3f}, "
            f"samples={self._trained_samples}, classes={self._classes}"
        )
        return {
            "samples":       self._trained_samples,
            "accuracy":      self._accuracy,
            "classes":       self._classes,
            "training_time": round(self._training_time, 3),
        }

    async def _detect_provider_async(self) -> None:
        """Détecte de manière asynchrone le premier fournisseur d'embedding disponible."""
        if self._detected_provider is not None:
            return

        local_targets = [
            {
                "type": "ollama",
                "url": "http://${OLLAMA_HOST:-localhost}:11434/api/embeddings",
                "payload": {"model": "nomic-embed-text", "prompt": "ping"},
                "model": "nomic-embed-text"
            },
            {
                "type": "ollama",
                "url": "http://${OLLAMA_HOST:-localhost}:11434/api/embeddings",
                "payload": {"model": "nomic-embed-text", "prompt": "ping"},
                "model": "nomic-embed-text"
            },
            {
                "type": "lmstudio",
                "url": "http://${LMSTUDIO_HOST:-localhost}:1234/v1/embeddings",
                "payload": {"model": "nomic-embed-text", "input": ["ping"]},
                "model": "nomic-embed-text"
            }
        ]

        # 1. Tester les cibles locales avec timeout très agressif
        for target in local_targets:
            try:
                timeout = aiohttp.ClientTimeout(total=2, connect=1)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(target["url"], json=target["payload"]) as resp:
                        if resp.status == 200:
                            self._detected_provider = target["type"]
                            self._detected_url = target["url"]
                            self._detected_model = target["model"]
                            logger.info(f"[ML ROUTER] Service d'embedding actif détecté : {target['type']} ({target['url']})")
                            return
            except Exception as e:
                logger.debug(f"[ML ROUTER] Hôte hors ligne {target['url']} : {e}")

        # 2. Tester Gemini API
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_key:
            try:
                from core.key_pool import get_key_pool
                pool = get_key_pool()
                if pool:
                    gemini_key = pool.get_free_key()
            except Exception:
                pass
        
        if gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={gemini_key}"
            payload = {
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": "ping"}]}
            }
            try:
                timeout = aiohttp.ClientTimeout(total=3)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=payload) as resp:
                        if resp.status == 200:
                            self._detected_provider = "gemini"
                            self._detected_url = "https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004"
                            self._detected_model = "text-embedding-004"
                            self._detected_key = gemini_key
                            logger.info("[ML ROUTER] Service d'embedding actif détecté : Gemini API (Cloud)")
                            return
            except Exception as e:
                logger.debug(f"[ML ROUTER] Gemini API non joignable ou clé expirée : {e}")

        # 3. Fallback TF-IDF
        self._detected_provider = "tfidf"
        logger.info("[ML ROUTER] Aucun service d'embedding actif trouvé. Fallback vers TF-IDF local.")

    def _detect_provider_sync(self) -> None:
        """Détecte de manière synchrone le premier fournisseur d'embedding disponible."""
        if self._detected_provider is not None:
            return

        import requests
        local_targets = [
            {
                "type": "ollama",
                "url": "http://${OLLAMA_HOST:-localhost}:11434/api/embeddings",
                "payload": {"model": "nomic-embed-text", "prompt": "ping"},
                "model": "nomic-embed-text"
            },
            {
                "type": "ollama",
                "url": "http://${OLLAMA_HOST:-localhost}:11434/api/embeddings",
                "payload": {"model": "nomic-embed-text", "prompt": "ping"},
                "model": "nomic-embed-text"
            },
            {
                "type": "lmstudio",
                "url": "http://${LMSTUDIO_HOST:-localhost}:1234/v1/embeddings",
                "payload": {"model": "nomic-embed-text", "input": ["ping"]},
                "model": "nomic-embed-text"
            }
        ]

        # 1. Cibles locales
        for target in local_targets:
            try:
                resp = requests.post(target["url"], json=target["payload"], timeout=1.5)
                if resp.status_code == 200:
                    self._detected_provider = target["type"]
                    self._detected_url = target["url"]
                    self._detected_model = target["model"]
                    logger.info(f"[ML ROUTER] Service d'embedding actif détecté (sync) : {target['type']} ({target['url']})")
                    return
            except Exception as e:
                logger.debug(f"[ML ROUTER] Hôte sync hors ligne {target['url']} : {e}")

        # 2. Gemini API
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_key:
            try:
                from core.key_pool import get_key_pool
                pool = get_key_pool()
                if pool:
                    gemini_key = pool.get_free_key()
            except Exception:
                pass
        
        if gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={gemini_key}"
            payload = {
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": "ping"}]}
            }
            try:
                resp = requests.post(url, json=payload, timeout=2.0)
                if resp.status_code == 200:
                    self._detected_provider = "gemini"
                    self._detected_url = "https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004"
                    self._detected_model = "text-embedding-004"
                    self._detected_key = gemini_key
                    logger.info("[ML ROUTER] Service d'embedding actif détecté (sync) : Gemini API (Cloud)")
                    return
            except Exception as e:
                logger.debug(f"[ML ROUTER] Gemini API sync non joignable : {e}")

        self._detected_provider = "tfidf"
        logger.info("[ML ROUTER] Aucun service d'embedding actif trouvé (sync). Fallback vers TF-IDF local.")

    async def _batch_embed(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Récupère les embeddings en batch depuis le fournisseur actif."""
        if not texts:
            return None

        if self._detected_provider is None:
            await self._detect_provider_async()

        if self._detected_provider == "tfidf" or self._detected_provider is None:
            return None

        timeout = aiohttp.ClientTimeout(total=EMBEDDING_TIMEOUT * len(texts) + 10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                embeddings = []

                if self._detected_provider == "ollama":
                    # Requêtes concurrentes pour simuler le batch sur Ollama embeddings
                    async def get_single_ollama(text):
                        async with session.post(
                            self._detected_url,
                            json={"model": self._detected_model, "prompt": text}
                        ) as r:
                            if r.status == 200:
                                d = await r.json()
                                return d["embedding"]
                            raise Exception(f"Ollama status {r.status}")

                    for i in range(0, len(texts), 10):
                        batch = texts[i:i + 10]
                        tasks = [get_single_ollama(t) for t in batch]
                        batch_embs = await asyncio.gather(*tasks)
                        embeddings.extend(batch_embs)
                    return embeddings

                elif self._detected_provider == "lmstudio":
                    for i in range(0, len(texts), 10):
                        batch = texts[i:i + 10]
                        async with session.post(
                            self._detected_url,
                            json={"input": batch, "model": self._detected_model}
                        ) as r:
                            if r.status != 200:
                                logger.warning(f"[ML ROUTER] LM Studio HTTP {r.status}")
                                return None
                            d = await r.json()
                            for item in d.get("data", []):
                                embeddings.append(item["embedding"])
                    return embeddings

                elif self._detected_provider == "gemini":
                    for i in range(0, len(texts), 100):
                        batch = texts[i:i + 100]
                        requests_payload = []
                        for text in batch:
                            requests_payload.append({
                                "model": f"models/{self._detected_model}",
                                "content": {"parts": [{"text": text[:40000]}]},
                                "taskType": "CLASSIFICATION",
                            })
                        payload = {"requests": requests_payload}
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._detected_model}:batchEmbedContents?key={self._detected_key}"
                        async with session.post(url, json=payload) as r:
                            if r.status != 200:
                                logger.warning(f"[ML ROUTER] Gemini batch HTTP {r.status}")
                                return None
                            d = await r.json()
                            for emb in d.get("embeddings", []):
                                embeddings.append(emb.get("values", []))
                    return embeddings

        except Exception as e:
            logger.debug(f"[ML ROUTER] Embedding batch échoué : {e}")
            self._detected_provider = None
        return None

    # ──────────────────────────────────────────────────────────────────
    # Prédiction
    # ──────────────────────────────────────────────────────────────────

    def predict(self, user_prompt: str) -> Tuple[Optional[str], float]:
        """
        Prédit le routing_type pour un prompt.

        Returns:
            (routing_type, confidence) ou (None, 0.0) si confiance < seuil
            ou modèle non entraîné → fallback LLM dans Router
        """
        if not self.is_trained or not user_prompt.strip():
            return (None, 0.0)

        try:
            if self._use_embeddings:
                X = self._embed_sync(user_prompt)
                if X is None:
                    return (None, 0.0)
            else:
                if self._vectorizer is None:
                    return (None, 0.0)
                X = self._vectorizer.transform([user_prompt])

            probas      = self._model.predict_proba(X)[0]
            best_idx    = int(np.argmax(probas))
            confidence  = float(probas[best_idx])

            if confidence < CONFIDENCE_THRESHOLD:
                logger.debug(
                    f"[ML ROUTER] Confiance {confidence:.3f} < {CONFIDENCE_THRESHOLD} → LLM"
                )
                return (None, 0.0)

            predicted = self._classes[best_idx]
            logger.info(f"[ML ROUTER] ⚡ ML predict={predicted} (conf={confidence:.3f})")
            return (predicted, confidence)

        except Exception as e:
            logger.error(f"[ML ROUTER] Erreur prédiction : {e}")
            return (None, 0.0)

    def _embedding_space_ok(self, produced_dim: int) -> bool:
        """[#T12] Vérifie que l'embedding d'inférence vit dans le MÊME espace qu'à l'entraînement.

        Le provider est re-détecté à l'inférence et peut différer de celui de
        l'entraînement. Deux modèles d'embedding distincts partagent parfois la même
        dimension (nomic-embed-text et text-embedding-004 = 768d) : sans ce contrôle,
        le classifieur reçoit des vecteurs d'un espace étranger et prédit des
        catégories silencieusement fausses. En cas de divergence → on rejette
        (predict() retourne (None, 0.0) → fallback vers le router LLM, comportement sûr).
        """
        # 1. Dimension — robuste même pour les modèles legacy (n_features_in_ sklearn).
        expected_dim = self._embedding_dim
        if expected_dim is None and self._model is not None:
            expected_dim = getattr(self._model, "n_features_in_", None)
        if expected_dim is not None and produced_dim != expected_dim:
            logger.warning(
                f"[ML ROUTER] Dimension d'embedding {produced_dim} ≠ {expected_dim} "
                f"(entraînement) → rejet, fallback LLM."
            )
            return False
        # 2. Identité du modèle — capte le cas pernicieux même-dimension / espace différent.
        if (self._embedding_model is not None and self._detected_model is not None
                and self._detected_model != self._embedding_model):
            logger.warning(
                f"[ML ROUTER] Modèle d'embedding détecté '{self._detected_model}' ≠ "
                f"'{self._embedding_model}' (entraînement) → rejet, fallback LLM."
            )
            return False
        return True

    def _embed_sync(self, text: str) -> Optional[np.ndarray]:
        """Embedding synchrone (requests) pour predict() appelé en contexte sync."""
        if self._detected_provider is None:
            self._detect_provider_sync()

        if self._detected_provider == "tfidf" or self._detected_provider is None:
            return None

        import requests
        emb = None
        try:
            if self._detected_provider == "ollama":
                resp = requests.post(
                    self._detected_url,
                    json={"model": self._detected_model, "prompt": text},
                    timeout=EMBEDDING_TIMEOUT,
                )
                if resp.status_code == 200:
                    emb = resp.json()["embedding"]

            elif self._detected_provider == "lmstudio":
                resp = requests.post(
                    self._detected_url,
                    json={"input": [text], "model": self._detected_model},
                    timeout=EMBEDDING_TIMEOUT,
                )
                if resp.status_code == 200:
                    emb = resp.json()["data"][0]["embedding"]

            elif self._detected_provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._detected_model}:embedContent?key={self._detected_key}"
                payload = {
                    "model": f"models/{self._detected_model}",
                    "content": {"parts": [{"text": text[:40000]}]},
                    "taskType": "CLASSIFICATION",
                }
                resp = requests.post(url, json=payload, timeout=EMBEDDING_TIMEOUT)
                if resp.status_code == 200:
                    emb = resp.json()["embedding"]["values"]

        except Exception as e:
            logger.debug(f"[ML ROUTER] Embedding sync échoué : {e}")
            self._detected_provider = None
            return None

        if emb is None:
            return None
        # [#T12] Garde-fou : rejette tout embedding hors de l'espace d'entraînement.
        if not self._embedding_space_ok(len(emb)):
            return None
        return np.array([emb])


# ──────────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────────

def get_ml_router() -> MLRouter:
    """Retourne l'instance singleton du MLRouter (chargement lazy)."""
    global _ml_router_instance
    if _ml_router_instance is None:
        _ml_router_instance = MLRouter()
    return _ml_router_instance
