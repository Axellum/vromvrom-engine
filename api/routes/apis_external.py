"""
api/routes/apis_external.py — Routes API des services externes du Moteur.

Extrait de gui_server.py lors du refactoring Semaine 3.
Contient : /api/apis-status, /api/generate-image, /api/tts, /api/tts-cloud,
           /api/translate, /api/vision/analyze, /api/aqa, /api/batch/*,
           /api/key-pool, /api/google-cache-status

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import logging
import os
import requests
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

logger = logging.getLogger(__name__)

router = APIRouter(tags=["APIs Externes"])


# ──────────────────────────────────────────────────────────────────
# Modèles Pydantic
# ──────────────────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = "fr-FR-Wavenet-A"
    speed: Optional[float] = 1.0


class TTSCloudRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None
    provider: Optional[str] = "gcp"


class TranslateRequest(BaseModel):
    text: str
    target_language: str
    source_language: Optional[str] = None


class VisionRequest(BaseModel):
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    prompt: str = "Décris cette image en détail."


class ImageGenerateRequest(BaseModel):
    prompt: str
    width: Optional[int] = 1024
    height: Optional[int] = 1024
    model: Optional[str] = None


class AQARequest(BaseModel):
    question: str
    context: Optional[str] = None


class BatchSubmitRequest(BaseModel):
    prompts: List[str]
    model: Optional[str] = "gemini-2.5-flash"
    system_prompt: Optional[str] = None


# ──────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────

from core.antigravity_helper import (
    get_antigravity_status
)



@router.get("/api/apis-status")
def get_apis_status():
    """Récupère l'état et les soldes de toutes les APIs configurées."""
    from core import token_tracker

    # 1. DeepSeek
    ds_key = os.environ.get("DEEPSEEK_API_KEY")
    ds_balance = None
    ds_error = None
    ds_configured = bool(ds_key)

    if ds_configured:
        try:
            headers = {"Authorization": f"Bearer {ds_key}", "Content-Type": "application/json"}
            res = requests.get("https://api.deepseek.com/user/balance", headers=headers, timeout=5)
            if res.status_code == 200:
                ds_balance = res.json()
            else:
                ds_error = f"HTTP {res.status_code}: {res.text}"
        except Exception as e:
            ds_error = str(e)

    # 2. Gemini
    gem_key = os.environ.get("GEMINI_API_KEY")
    gem_project = os.environ.get("GEMINI_PROJECT_NAME", "projects/487151672026")
    gem_active = False
    gem_error = None
    gem_configured = bool(gem_key)

    if gem_configured:
        try:
            res = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={gem_key}",
                timeout=5
            )
            gem_active = res.status_code == 200
            if not gem_active:
                gem_error = f"HTTP {res.status_code}: {res.text}"
        except Exception as e:
            gem_error = str(e)

    # Masquage des clés
    obfuscated_gem_key = f"{gem_key[:6]}...{gem_key[-6:]}" if gem_key and len(gem_key) > 10 else ("***" if gem_key else "Non configurée")
    obfuscated_ds_key = f"{ds_key[:6]}...{ds_key[-6:]}" if ds_key and len(ds_key) > 10 else ("***" if ds_key else "Non configurée")

    # Facturation réelle
    try:
        usage_data = token_tracker.load_usage()
    except Exception:
        usage_data = {}
    real_billing = usage_data.get("real_billing", {})

    if ds_balance and ds_balance.get("is_available") and ds_balance.get("balance_infos"):
        total_ds_usd = 0.0
        for info in ds_balance["balance_infos"]:
            try:
                amt = float(info["total_balance"])
                total_ds_usd += amt * 0.14 if info["currency"] == "CNY" else amt
            except Exception:
                pass
        try:
            token_tracker.update_real_billing(deepseek_balance_usd=total_ds_usd)
        except Exception:
            pass
        real_billing["deepseek_balance_usd"] = total_ds_usd
        real_billing["deepseek_last_sync"] = datetime.now().isoformat()

    ds_active = bool(ds_balance and ds_balance.get("is_available") and not ds_error)

    try:
        antigravity_info = get_antigravity_status()
    except Exception as e:
        antigravity_info = {
            "connected": False, "user": "Erreur", "email": "Erreur",
            "plan": "Erreur", "credits": 0.0, "error": str(e)
        }

    return {
        "deepseek": {
            "configured": ds_configured, "active": ds_active, "obfuscated_key": obfuscated_ds_key,
            "balance": ds_balance, "error": ds_error,
            "real_balance_usd": real_billing.get("deepseek_balance_usd", 0.0),
            "last_sync": real_billing.get("deepseek_last_sync")
        },
        "gemini": {
            "configured": gem_configured, "obfuscated_key": obfuscated_gem_key, "active": gem_active,
            "project_name": gem_project, "error": gem_error,
            "real_cost_usd": real_billing.get("gemini_gcp_cost_usd", 0.0),
            "last_sync": real_billing.get("gemini_gcp_last_sync")
        },
        "claude": {
            "real_usage_pct": real_billing.get("claude_message_usage_pct"),
            "summary_text": real_billing.get("claude_summary_text"),
            "last_sync": real_billing.get("claude_last_sync")
        },
        "antigravity": antigravity_info
    }


@router.get("/api/google-cache-status")
async def get_google_cache_status():
    """Retourne le statut du cache Gemini (Content Cache API)."""
    try:
        from core.gcp_oauth import get_cache_status
        return await get_cache_status()
    except ImportError:
        return {"error": "Module gcp_oauth non disponible", "cache": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/key-pool")
def get_key_pool():
    """Retourne l'état du pool de clés API (rotation OAuth GCP)."""
    try:
        from core.gcp_oauth import get_key_pool_status
        return get_key_pool_status()
    except ImportError:
        return {"error": "Module gcp_oauth non disponible", "keys": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/generate-image")
async def generate_image_api(body: ImageGenerateRequest):
    """Génère une image via Imagen (GCP) ou Gemini."""
    try:
        from core.gemini_native import generate_image
        result = await generate_image(body.prompt, width=body.width, height=body.height, model=body.model)
        return result
    except ImportError:
        raise HTTPException(status_code=501, detail="Module génération d'images non disponible.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tts")
async def text_to_speech(body: TTSRequest):
    """Synthèse vocale via GCP Text-to-Speech (Wavenet)."""
    try:
        from core.gcp_tts import synthesize_speech
        audio_data = await synthesize_speech(body.text, voice=body.voice, speed=body.speed)
        return {"status": "ok", "audio_base64": audio_data}
    except ImportError:
        raise HTTPException(status_code=501, detail="Module TTS non disponible.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tts-cloud")
async def tts_cloud(body: TTSCloudRequest):
    """Synthèse vocale multi-provider (ElevenLabs, GCP, etc.)."""
    try:
        from core.tts_router import synthesize
        result = await synthesize(body.text, voice_id=body.voice_id, provider=body.provider)
        return result
    except ImportError:
        raise HTTPException(status_code=501, detail="Module TTS Cloud non disponible.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/tts-cloud/voices")
async def get_tts_voices():
    """Retourne les voix disponibles pour la synthèse vocale cloud."""
    try:
        from core.tts_router import get_available_voices
        return await get_available_voices()
    except ImportError:
        return {"voices": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/translate")
async def translate_text(body: TranslateRequest):
    """Traduction de texte via GCP Translate API."""
    try:
        from core.gcp_translate import translate
        result = await translate(body.text, body.target_language, body.source_language)
        return {"translated": result, "target": body.target_language}
    except ImportError:
        raise HTTPException(status_code=501, detail="Module traduction non disponible.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/vision/analyze")
async def vision_analyze(body: VisionRequest):
    """Analyse d'image via Gemini Vision."""
    try:
        from core.gemini_native import analyze_image
        result = await analyze_image(
            prompt=body.prompt,
            image_url=body.image_url,
            image_base64=body.image_base64,
        )
        return {"analysis": result}
    except ImportError:
        raise HTTPException(status_code=501, detail="Module Vision non disponible.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/aqa")
async def attributed_question_answering(body: AQARequest):
    """[Gemini AQA] Réponse à question ancrée sur contexte (Attributed QA)."""
    try:
        from core.gemini_native import aqa_query
        result = await aqa_query(body.question, context=body.context)
        return result
    except ImportError:
        raise HTTPException(status_code=501, detail="Module AQA non disponible.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────
# Batch (traitement asynchrone de lots de prompts)
# ──────────────────────────────────────────────────────────────────

@router.post("/api/batch/submit")
async def batch_submit(body: BatchSubmitRequest):
    """Soumet un lot de prompts pour traitement batch asynchrone."""
    try:
        from core.batch_processor import submit_batch
        job_id = await submit_batch(body.prompts, model=body.model, system_prompt=body.system_prompt)
        return {"status": "submitted", "job_id": job_id}
    except ImportError:
        raise HTTPException(status_code=501, detail="Module batch non disponible.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/batch/status/{job_id}")
def batch_status(job_id: str):
    """Retourne le statut d'un job batch."""
    try:
        from core.batch_processor import get_job_status
        status = get_job_status(job_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' introuvable.")
        return status
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/batch/results/{job_id}")
def batch_results(job_id: str):
    """Retourne les résultats d'un job batch terminé."""
    try:
        from core.batch_processor import get_job_results
        results = get_job_results(job_id)
        if results is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' introuvable ou non terminé.")
        return results
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/batch/list")
def batch_list(limit: int = 20):
    """Retourne la liste des jobs batch récents."""
    try:
        from core.batch_processor import list_jobs
        return {"jobs": list_jobs(limit=limit)}
    except ImportError:
        return {"jobs": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
