"""
tools/finetune_qlora_local.py — Fine-tuning QLoRA local sur la RTX 5070 Ti.

Entraîne un adaptateur LoRA (4-bit QLoRA) sur les datasets produits par
`vertex_dataset_factory.py` (dataset_moteur_clean.jsonl, dataset_tab5_clean.jsonl).
GRATUIT, hors crédit GCP : le crédit a servi à GÉNÉRER les données (Gemini Pro/Flash
à grande échelle), ta carte sert à les APPRENDRE. Résultat : un modèle spécialisé
domotique exportable vers LM Studio.

────────────────────────────────────────────────────────────────────────────
PRÉREQUIS (RTX 5070 Ti = Blackwell sm_120 → builds CUDA récents OBLIGATOIRES)
────────────────────────────────────────────────────────────────────────────
  # PyTorch avec CUDA 12.8+ (Blackwell). Vérifier l'index officiel à jour.
  pip install --index-url https://download.pytorch.org/whl/cu128 torch
  pip install transformers peft trl bitsandbytes datasets accelerate
  # bitsandbytes >= 0.45 pour le support Blackwell (4-bit).
  python -c "import torch; print(torch.cuda.get_device_name(0), torch.cuda.is_available())"

Mémoire : un modèle 7B en 4-bit + LoRA tient confortablement dans 16 Go
(seq_len 1024, batch 1 + grad accumulation). Pour un 9B, baisser seq_len/batch.

────────────────────────────────────────────────────────────────────────────
USAGE
────────────────────────────────────────────────────────────────────────────
  python tools/finetune_qlora_local.py \
      --data dataset_moteur_clean.jsonl dataset_tab5_clean.jsonl \
      --base Qwen/Qwen2.5-Coder-7B-Instruct \
      --out ./lora_domotique --epochs 2

Modèles de base conseillés (tous compatibles 4-bit/16 Go) :
  - Qwen/Qwen2.5-Coder-7B-Instruct  (défaut : excellent code, bon FR)
  - google/gemma-2-9b-it            (multilingue FR fort ; baisser seq_len)
  - meta-llama/Llama-3.1-8B-Instruct

Après entraînement → export GGUF pour LM Studio :
  1) python merge_adapter (option --merge) pour fusionner LoRA + base.
  2) llama.cpp : python convert_hf_to_gguf.py ./lora_domotique/merged \
       --outfile domotique.gguf --outtype q4_k_m
  3) Déposer le .gguf dans le dossier modèles de LM Studio.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────────
# Chargement & formatage des données
# ──────────────────────────────────────────────────────────────────────────

def load_pairs(paths: list[Path]) -> list[dict]:
    """Charge et fusionne les datasets .jsonl (schéma instruction/input/output)."""
    rows: list[dict] = []
    for p in paths:
        if not p.exists():
            print(f"AVERTISSEMENT: {p} introuvable, ignoré.")
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ex = json.loads(line)
                except json.JSONDecodeError:
                    continue
                instr = ex.get("instruction", "")
                out = ex.get("output", "")
                if instr and out:
                    rows.append({"instruction": instr,
                                 "input": ex.get("input", ""),
                                 "output": out})
    return rows


def to_chat(ex: dict) -> dict:
    """Formate une paire au format messages (chat template du tokenizer)."""
    user = ex["instruction"]
    if ex.get("input"):
        user += f"\n\n{ex['input']}"
    return {"messages": [
        {"role": "user", "content": user},
        {"role": "assistant", "content": ex["output"]},
    ]}


# ──────────────────────────────────────────────────────────────────────────
# Entraînement
# ──────────────────────────────────────────────────────────────────────────

def train(args: argparse.Namespace) -> None:
    # Imports tardifs : ces dépendances sont lourdes et optionnelles.
    from datasets import Dataset
    import torch
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig)
    from peft import LoraConfig
    from trl import SFTTrainer, SFTConfig

    if not torch.cuda.is_available():
        print("ERREUR: CUDA indisponible. Vérifie l'install PyTorch Blackwell (cu128).")
        sys.exit(1)
    print(f"GPU : {torch.cuda.get_device_name(0)}")

    # 1) Données → format chat → split train/eval.
    rows = load_pairs([Path(p) for p in args.data])
    if not rows:
        print("ERREUR: aucune paire chargée.")
        sys.exit(1)
    print(f"Paires chargées : {len(rows)}")
    ds = Dataset.from_list([to_chat(r) for r in rows]).train_test_split(
        test_size=args.eval_frac, seed=42)

    # 2) Quantization 4-bit (QLoRA) : la base reste figée en NF4, on entraîne
    #    seulement les adaptateurs LoRA → tient dans 16 Go.
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    tok = AutoTokenizer.from_pretrained(args.base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.base, quantization_config=bnb, device_map="auto",
        torch_dtype=torch.bfloat16,
    )

    # 3) Cible LoRA : projections attention + MLP (standard, robuste cross-archi).
    lora = LoraConfig(
        r=args.lora_r, lora_alpha=args.lora_r * 2, lora_dropout=0.05,
        bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    # 4) Config SFT. packing=True regroupe les exemples courts → meilleur débit.
    cfg = SFTConfig(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        bf16=True,
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        max_length=args.seq_len,
        packing=True,
        gradient_checkpointing=True,
        optim="paged_adamw_8bit",   # optimiseur pagé : économise la VRAM
        warmup_ratio=0.03,
        lr_scheduler_type="cosine",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model, args=cfg, peft_config=lora,
        train_dataset=ds["train"], eval_dataset=ds["test"],
        processing_class=tok,
    )
    print(f"Démarrage entraînement : {args.epochs} epoch(s), "
          f"batch {args.batch}×{args.grad_accum}, seq_len {args.seq_len}")
    trainer.train()
    trainer.save_model(args.out)
    tok.save_pretrained(args.out)
    print(f"[OK] Adaptateur LoRA sauvegardé → {args.out}")

    # 5) Fusion optionnelle base+LoRA (pour export GGUF/LM Studio).
    if args.merge:
        print("Fusion base + LoRA (peut nécessiter de la RAM/VRAM)...")
        from peft import PeftModel
        base = AutoModelForCausalLM.from_pretrained(
            args.base, torch_dtype=torch.bfloat16, device_map="cpu")
        merged = PeftModel.from_pretrained(base, args.out).merge_and_unload()
        merged_dir = Path(args.out) / "merged"
        merged.save_pretrained(merged_dir)
        tok.save_pretrained(merged_dir)
        print(f"[OK] Modèle fusionné → {merged_dir}\n"
              f"     Convertir en GGUF avec llama.cpp puis déposer dans LM Studio.")


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", nargs="+", required=True,
                   help="Un ou plusieurs .jsonl (moteur + Tab5).")
    p.add_argument("--base", default="Qwen/Qwen2.5-Coder-7B-Instruct",
                   help="Modèle de base HuggingFace.")
    p.add_argument("--out", default="./lora_domotique")
    p.add_argument("--epochs", type=float, default=2)
    p.add_argument("--batch", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=16)
    p.add_argument("--seq-len", type=int, default=1024)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--eval-frac", type=float, default=0.05)
    p.add_argument("--merge", action="store_true",
                   help="Fusionner base+LoRA après entraînement (pour GGUF).")
    args = p.parse_args()
    train(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
