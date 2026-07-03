"""Fusionne un checkpoint LoRA avec sa base et sauvegarde le modèle complet.

Utile quand un entraînement crashe après un checkpoint d'epoch : on récupère
un modèle exploitable sans relancer. Merge sur CPU (device_map="cpu") pour ne
pas entrer en conflit avec la VRAM d'un autre run.

Usage :
  python tools/merge_lora_checkpoint.py \
    --base Qwen/Qwen2.5-Coder-7B-Instruct \
    --adapter lora_domotique_full/checkpoint-470 \
    --out lora_domotique_full/merged
"""
import argparse
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    p.add_argument("--adapter", required=True, help="Dossier du checkpoint LoRA")
    p.add_argument("--out", required=True, help="Dossier de sortie du modèle fusionné")
    args = p.parse_args()

    print(f"Chargement base {args.base} (bf16, CPU)...")
    base = AutoModelForCausalLM.from_pretrained(
        args.base, torch_dtype=torch.bfloat16, device_map="cpu")
    print(f"Application de l'adaptateur {args.adapter}...")
    merged = PeftModel.from_pretrained(base, args.adapter).merge_and_unload()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(out_dir)
    AutoTokenizer.from_pretrained(args.base).save_pretrained(out_dir)
    print(f"[OK] Modèle fusionné -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
