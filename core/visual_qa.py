"""
core/visual_qa.py — Service de capture et d'analyse visuelle multimodale.

Vision Engine pour le ReviewerAgent.

Permet au Reviewer de valider visuellement les interfaces générées en :
1. Capturant un screenshot via Puppeteer (serveur MCP)
2. Envoyant le screenshot à un LLM multimodal pour analyse
3. Retournant un verdict visuel structuré

Pour les interfaces ESPHome/LVGL (non capturables par Puppeteer), le service
bascule en mode d'analyse textuelle enrichie (extraction de couleurs, tailles,
disposition depuis le code YAML/C++).

Modèles multimodaux utilisables :
- gemini-3.5-flash (API gratuite, supporte les images)
- claude-sonnet-4-6 via CLI (inclus dans l'abonnement Pro)
"""

import os
import base64
import logging
import asyncio
import json
import subprocess
import tempfile
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class VisualQAService:
    """
    Service de capture d'écran et d'analyse visuelle multimodale.

    Utilisé par le ReviewLoop pour enrichir la revue de code avec
    un verdict visuel quand la tâche produit une interface utilisateur.
    """

    def __init__(self, llm_gateway=None):
        """
        Args:
            llm_gateway: Instance de LLMGateway pour l'appel multimodal.
                         Si None, le service tente l'import lazy.
        """
        self._gateway = llm_gateway
        # URL par défaut de l'IHM du moteur pour les auto-captures
        self._default_url = "http://localhost:8765"

    async def capture_and_analyze(
        self,
        url: Optional[str] = None,
        question: str = "Analyse cette interface. Est-elle visuellement correcte, harmonieuse et ergonomique ?",
        model_tier: str = "moyen",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Capture un screenshot et l'analyse via un LLM multimodal.

        Args:
            url: URL à capturer (défaut : IHM locale)
            question: Question d'analyse visuelle
            model_tier: Tier du modèle pour l'analyse
            session_id: ID de session pour le tracking

        Returns:
            Dict avec les clés :
            - success (bool) : True si l'analyse a réussi
            - screenshot_path (str|None) : Chemin du screenshot capturé
            - visual_verdict (str) : Verdict visuel textuel
            - issues (list[str]) : Problèmes visuels détectés
            - score (int) : Score de qualité visuelle /10
        """
        target_url = url or self._default_url

        # Étape 1 : Capture du screenshot
        screenshot_b64 = await self.capture_screenshot(target_url)

        screenshot_path = None
        if screenshot_b64:
            # Enregistrer dans static/screenshots/<session_id>.png
            try:
                screenshots_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "static", "screenshots"
                )
                os.makedirs(screenshots_dir, exist_ok=True)
                # Fallback session_id s'il est manquant
                s_id = session_id or f"temp_{int(asyncio.get_event_loop().time())}"
                file_path = os.path.join(screenshots_dir, f"{s_id}.png")
                with open(file_path, "wb") as f:
                    f.write(base64.b64decode(screenshot_b64))
                screenshot_path = f"/static/screenshots/{s_id}.png"
                logger.info(f"[VISUAL-QA] Screenshot sauvegardé localement : {file_path}")
            except Exception as e:
                logger.error(f"[VISUAL-QA] Erreur d'écriture du screenshot : {e}")

        if not screenshot_b64:
            logger.warning(
                f"[VISUAL-QA] Capture d'écran échouée pour {target_url}. "
                "Analyse en mode textuel uniquement."
            )
            return {
                "success": False,
                "screenshot_path": None,
                "visual_verdict": "Capture d'écran impossible — analyse visuelle non disponible.",
                "issues": ["Puppeteer non disponible ou URL inaccessible"],
                "score": -1,
            }

        # Étape 2 : Analyse multimodale du screenshot
        analysis = await self.analyze_image(
            image_base64=screenshot_b64,
            question=question,
            model_tier=model_tier,
            session_id=session_id,
        )

        analysis["screenshot_path"] = screenshot_path
        return analysis

    async def capture_screenshot(self, url: str) -> Optional[str]:
        """
        Capture un screenshot via Puppeteer (subprocess Node.js).

        Utilise un script inline Node.js minimal avec puppeteer-core
        pour se connecter au navigateur Chrome de débogage (port 9222).

        Args:
            url: URL à capturer

        Returns:
            Screenshot encodé en base64 (str), ou None si échec.
        """
        # Script Node.js minimal pour la capture via puppeteer-core
        # Se connecte au Chrome lancé avec --remote-debugging-port=9222
        node_script = f"""
const puppeteer = require('puppeteer-core');
(async () => {{
    let browser;
    try {{
        browser = await puppeteer.connect({{
            browserURL: 'http://127.0.0.1:9222',
            defaultViewport: {{ width: 1280, height: 800 }}
        }});
        const page = await browser.newPage();
        await page.goto('{url}', {{ waitUntil: 'networkidle0', timeout: 15000 }});
        await new Promise(r => setTimeout(r, 2000)); // Attendre le rendu
        const screenshot = await page.screenshot({{ encoding: 'base64' }});
        console.log(screenshot);
        await page.close();
    }} catch (e) {{
        console.error('PUPPETEER_ERROR:' + e.message);
        process.exit(1);
    }} finally {{
        if (browser) await browser.disconnect();
    }}
}})();
"""
        # Écrire le script dans un fichier temporaire
        script_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".js", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(node_script)
                script_file = tmp.name

            # Exécuter le script Node.js
            result = await asyncio.to_thread(
                subprocess.run,
                ["node", script_file],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )

            if result.returncode == 0 and result.stdout.strip():
                screenshot_b64 = result.stdout.strip()
                # Vérifier que c'est bien du base64 valide
                try:
                    decoded = base64.b64decode(screenshot_b64)
                    if len(decoded) > 1000:  # Au moins 1Ko = image valide
                        logger.info(
                            f"[VISUAL-QA] Screenshot capturé avec succès "
                            f"({len(decoded)} bytes)"
                        )
                        return screenshot_b64
                except Exception:
                    pass

            # Log de l'erreur
            stderr = result.stderr.strip() if result.stderr else ""
            if "PUPPETEER_ERROR:" in stderr:
                error_msg = stderr.split("PUPPETEER_ERROR:")[-1]
                logger.warning(f"[VISUAL-QA] Puppeteer : {error_msg}")
            else:
                logger.warning(
                    f"[VISUAL-QA] Capture échouée (code {result.returncode}): "
                    f"{stderr[:200]}"
                )

        except subprocess.TimeoutExpired:
            logger.warning("[VISUAL-QA] Timeout de capture (30s)")
        except FileNotFoundError:
            logger.warning("[VISUAL-QA] Node.js non trouvé dans le PATH")
        except Exception as e:
            logger.warning(f"[VISUAL-QA] Erreur de capture : {e}")
        finally:
            if script_file:
                try:
                    os.unlink(script_file)
                except Exception:
                    pass

        return None

    async def analyze_image(
        self,
        image_base64: str,
        question: str,
        model_tier: str = "moyen",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Analyse multimodale d'un screenshot via un LLM supportant la vision.

        Utilise l'API Gemini (qui supporte nativement les images en base64)
        pour analyser le contenu visuel du screenshot.

        Args:
            image_base64: Screenshot encodé en base64
            question: Question/consigne d'analyse
            model_tier: Tier du modèle pour l'analyse
            session_id: ID de session pour le tracking

        Returns:
            Dict structuré avec verdict, issues et score.
        """
        # Résolution du gateway
        gateway = self._gateway
        if not gateway:
            try:
                from core.llm_gateway import LLMGateway
                gateway = LLMGateway()
            except Exception as e:
                logger.error(f"[VISUAL-QA] Impossible d'instancier LLMGateway : {e}")
                return self._error_result("LLMGateway non disponible")

        # Prompt d'analyse visuelle structuré
        system_prompt = """Tu es un expert en design d'interfaces (UI/UX).
Analyse cette capture d'écran et évalue la qualité visuelle selon les critères suivants :
1. HARMONIE DES COULEURS : Les couleurs sont-elles cohérentes et agréables ?
2. TYPOGRAPHIE : Les polices sont-elles lisibles et bien hiérarchisées ?
3. ALIGNEMENT ET ESPACEMENT : Les éléments sont-ils bien alignés avec des marges cohérentes ?
4. ACCESSIBILITÉ : Le contraste est-il suffisant ? Les éléments interactifs sont-ils identifiables ?
5. COHÉRENCE : Le style est-il homogène sur toute l'interface ?
6. ERGONOMIE : La navigation est-elle intuitive ?

Tu DOIS répondre en JSON strict avec cette structure :
{
  "visual_verdict": "Ton verdict détaillé en 2-3 phrases",
  "issues": ["liste des problèmes visuels détectés"],
  "strengths": ["liste des points forts visuels"],
  "score": 7,
  "recommendations": ["liste de recommandations d'amélioration"]
}
Score sur 10 : 1-3 = médiocre, 4-5 = passable, 6-7 = correct, 8-9 = bon, 10 = excellent."""

        user_prompt = f"{question}\n\n[IMAGE JOINTE EN BASE64 — ANALYSE LE SCREENSHOT CI-DESSOUS]"

        try:
            # Tentative avec Gemini API (support natif des images)
            provider = gateway.providers.get("gemini-3.5-flash-free")
            if not provider:
                provider = gateway.providers.get("gemini-3.5-flash")
            if not provider:
                # Fallback sur n'importe quel provider Gemini
                for name, p in gateway.providers.items():
                    if "gemini" in name.lower() and "cli" not in name.lower():
                        provider = p
                        break

            if provider and hasattr(provider, "_call_gemini_api"):
                # Appel direct avec image multimodale
                result = await self._call_gemini_multimodal(
                    provider, system_prompt, user_prompt, image_base64, session_id
                )
                if result:
                    return result

            # Fallback : envoyer uniquement la description textuelle
            # (quand le provider ne supporte pas les images)
            logger.info(
                "[VISUAL-QA] Fallback mode textuel (provider sans support image)"
            )
            text_prompt = (
                f"{user_prompt}\n\n"
                "NOTE : Le screenshot n'a pas pu être transmis au modèle. "
                "Analyse basée sur le contexte textuel uniquement."
            )

            from core.llm_gateway import load_config
            config = load_config()
            _, fallback_provider = gateway.get_provider_for_tier(model_tier, config)

            response = await asyncio.to_thread(
                fallback_provider.generate_structured,
                system_prompt,
                text_prompt,
                {
                    "type": "object",
                    "properties": {
                        "visual_verdict": {"type": "string"},
                        "issues": {"type": "array", "items": {"type": "string"}},
                        "score": {"type": "integer"},
                    },
                },
                session_id=session_id,
            )

            return {
                "success": True,
                "screenshot_path": None,
                "visual_verdict": response.get("visual_verdict", "Analyse textuelle uniquement"),
                "issues": response.get("issues", []),
                "score": response.get("score", -1),
                "mode": "text_fallback",
            }

        except Exception as e:
            logger.error(f"[VISUAL-QA] Erreur d'analyse multimodale : {e}")
            return self._error_result(str(e))

    async def _call_gemini_multimodal(
        self, provider, system_prompt: str, user_prompt: str,
        image_base64: str, session_id: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Appel Gemini avec image multimodale via l'API REST directe.

        Gemini API accepte les images inline via le format :
        { "inlineData": { "mimeType": "image/png", "data": "<base64>" } }
        """
        import requests

        try:
            api_key = provider.api_key if hasattr(provider, "api_key") else None
            model = provider.model if hasattr(provider, "model") else "gemini-3.5-flash"

            if not api_key:
                return None

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
                f":generateContent?key={api_key}"
            )

            payload = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [
                    {
                        "parts": [
                            {"text": user_prompt},
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": image_base64,
                                }
                            },
                        ]
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.3,
                },
            }

            response = await asyncio.to_thread(
                requests.post, url, json=payload, timeout=60
            )

            if response.status_code == 200:
                data = response.json()
                text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "{}")
                )

                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = {"visual_verdict": text, "issues": [], "score": -1}

                # Tracking des tokens
                usage = data.get("usageMetadata", {})
                if usage:
                    try:
                        from core.token_tracker import record_usage
                        record_usage(
                            model,
                            usage.get("promptTokenCount", 0),
                            usage.get("candidatesTokenCount", 0),
                            session_id=session_id,
                        )
                    except Exception:
                        pass

                logger.info(
                    f"[VISUAL-QA] ✅ Analyse multimodale réussie — "
                    f"score: {parsed.get('score', '?')}/10"
                )

                return {
                    "success": True,
                    "screenshot_path": None,
                    "visual_verdict": parsed.get("visual_verdict", ""),
                    "issues": parsed.get("issues", []),
                    "strengths": parsed.get("strengths", []),
                    "score": parsed.get("score", -1),
                    "recommendations": parsed.get("recommendations", []),
                    "mode": "multimodal",
                }
            else:
                logger.warning(
                    f"[VISUAL-QA] Gemini API erreur {response.status_code}: "
                    f"{response.text[:200]}"
                )
                return None

        except Exception as e:
            logger.warning(f"[VISUAL-QA] Erreur appel Gemini multimodal : {e}")
            return None

    def analyze_lvgl_code(self, yaml_content: str, cpp_content: str = "") -> Dict[str, Any]:
        """
        Analyse textuelle enrichie d'un code LVGL (quand Puppeteer n'est pas applicable).

        Extrait les informations visuelles du code YAML ESPHome et C++ :
        - Couleurs (valeurs hex, RGB, noms)
        - Tailles (police, objets, marges)
        - Layout (grilles, flex, alignement)
        - Composants (boutons, labels, jauges)

        Args:
            yaml_content: Contenu du fichier YAML ESPHome
            cpp_content: Contenu du fichier C++ custom (optionnel)

        Returns:
            Dict avec analyse structurée du design.
        """
        import re

        analysis = {
            "colors": [],
            "fonts": [],
            "components": [],
            "layout": [],
            "warnings": [],
        }

        combined = yaml_content + "\n" + cpp_content

        # Extraction des couleurs (hex LVGL : 0xRRGGBB ou #RRGGBB)
        hex_colors = re.findall(r'(?:0x|#)([0-9A-Fa-f]{6})', combined)
        analysis["colors"] = list(set(hex_colors))

        # Extraction des tailles de police
        font_sizes = re.findall(r'text_font:\s*(\w+)', combined)
        analysis["fonts"] = list(set(font_sizes))

        # Extraction des composants LVGL
        lvgl_widgets = re.findall(
            r'(?:lv_|lvgl\.widget\.)(\w+)', combined
        )
        analysis["components"] = list(set(lvgl_widgets))

        # Détection de problèmes courants
        if len(analysis["colors"]) > 12:
            analysis["warnings"].append(
                f"Trop de couleurs distinctes ({len(analysis['colors'])}). "
                "Limiter à 5-7 pour la cohérence visuelle."
            )

        return analysis

    @staticmethod
    def _error_result(error_msg: str) -> Dict[str, Any]:
        """Retourne un résultat d'erreur formaté."""
        return {
            "success": False,
            "screenshot_path": None,
            "visual_verdict": f"Analyse visuelle échouée : {error_msg}",
            "issues": [error_msg],
            "score": -1,
        }
