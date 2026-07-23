#!/usr/bin/env python3
"""Scan anti-fuite pour le miroir public vromvrom-engine.

Bloque le CI si des IP LAN réelles (192.168.0.x), tokens ou clés API
ressemblant à du vrai matériel apparaissent dans les sources trackées.

Les placeholders documentés (192.168.1.x, changez-moi, AIza...votre_clé...)
sont autorisés.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Dossiers / fichiers hors périmètre (deps, caches, binaires).
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "chroma_db",
    "chromadb_data",
    "dist",
    "build",
    ".tox",
}

SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".db",
    ".sqlite3",
    ".pyc",
    ".pyo",
    ".whl",
    ".gz",
    ".zip",
}

# Extensions textuelles scannées.
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".json5",
    ".example",
    ".env",
    ".sh",
    ".ps1",
    ".js",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".ini",
    ".cfg",
    ".xml",
}

# Lignes explicitement autorisées (placeholders / docs).
ALLOW_LINE_PATTERNS = [
    re.compile(r"192\.168\.1\.x"),
    re.compile(r"\$\{HA_HOST:-192\.168\.1\.x\}"),
    re.compile(r"changez-moi", re.I),
    re.compile(r"AIza\.\.\.votre_clé", re.I),
    re.compile(r"votre_clé", re.I),
    re.compile(r"YOUR[_-]?API[_-]?KEY", re.I),
    re.compile(r"<MOTEUR_API_KEY>"),
    re.compile(r"Bearer <"),
    re.compile(r"sk-\.\.\."),
    # Fixtures de tests (sanitizer) — pas de vraies clés.
    re.compile(r"sk-abcdef[0-9A-Za-z]*"),
]

# Règles bloquantes : (nom, regex, message).
RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "lan_ip_0_x",
        re.compile(r"192\.168\.0\.\d+"),
        "IP LAN privée 192.168.0.x — anonymiser en 192.168.1.x / ${HA_HOST:-…}",
    ),
    (
        "openai_style_key",
        re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}"),
        "Clé style OpenAI/DeepSeek (sk-…) détectée",
    ),
    (
        "google_api_key",
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
        "Clé Google API (AIza…) détectée",
    ),
    (
        "github_pat_classic",
        re.compile(r"ghp_[A-Za-z0-9]{36}"),
        "GitHub PAT classique (ghp_…) détecté",
    ),
    (
        "github_pat_fine",
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
        "GitHub fine-grained PAT détecté",
    ),
    (
        "jwt_like",
        re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
        "JWT / token base64-like détecté",
    ),
    (
        "private_key_pem",
        re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
        "Clé privée PEM détectée",
    ),
    (
        "aws_access_key",
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "AWS Access Key ID détecté",
    ),
]


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    # Fichiers sans suffixe courants
    return path.name in {
        "Dockerfile",
        "Dockerfile.webhook",
        "Makefile",
        "LICENSE",
        ".gitignore",
        ".dockerignore",
        ".pre-commit-config.yaml",
    }


def _line_allowed(line: str) -> bool:
    return any(p.search(line) for p in ALLOW_LINE_PATTERNS)


def iter_source_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if not _is_text_file(path):
            continue
        out.append(path)
    return sorted(out)


def scan() -> list[str]:
    findings: list[str] = []
    for path in iter_source_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(f"{path.relative_to(ROOT)}: lecture impossible ({exc})")
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _line_allowed(line):
                continue
            for rule_id, pattern, message in RULES:
                if pattern.search(line):
                    rel = path.relative_to(ROOT).as_posix()
                    findings.append(f"{rel}:{lineno}: [{rule_id}] {message}")
                    break
    return findings


def main() -> int:
    findings = scan()
    if not findings:
        print("✅ oss_secrets_scan : aucun secret / IP LAN réelle détecté(e).")
        return 0
    print("⛔ oss_secrets_scan : fuites potentielles — NE PAS MERGER :", file=sys.stderr)
    for item in findings:
        print(f"  - {item}", file=sys.stderr)
    print(f"\n{len(findings)} alerte(s).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
