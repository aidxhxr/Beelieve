"""QLoRA fine-tune of Mistral-7B-Instruct-v0.3 into the Beelieve advisor.

Fits on a single 24 GB GPU: 4-bit NF4 quantized base, LoRA r=16/alpha=32 on
all attention + MLP projections, packed sequences, cosine schedule.

Usage (from services/recommender, after `python -m finetune.dataset`):
    python -m finetune.train_lora \
        --train-file finetune/data/train.jsonl \
        --output-dir finetune/out/beelieve-advisor-lora \
        --push   # optional: upload adapter to $HF_MODEL_ID using $HF_API_KEY
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_BASE_MODEL = os.environ.get("HF_BASE_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")
DEFAULT_HUB_REPO = os.environ.get("HF_MODEL_ID", "aidxhxr/beelieve-mistral-7b-advisor")

LORA_TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument(
        "--train-file", type=Path,
        default=Path(__file__).parent / "data" / "train.jsonl",
    )
    parser.add_argument(
        "--eval-file", type=Path,
        default=Path(__file__).parent / "data" / "eval.jsonl",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).parent / "out" / "beelieve-advisor-lora",
    )
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--push", action="store_true",
        help=f"push the trained adapter to the Hub repo (default {DEFAULT_HUB_REPO})",
    )
    parser.add_argument("--hub-repo", default=DEFAULT_HUB_REPO)
    return parser.parse_args()


def format_example(example: dict, tokenizer: AutoTokenizer) -> dict:
    """Render chat messages to a single training string.

    Mistral-Instruct chat templates historically reject a standalone system
    role; fold the system prompt into the first user turn when that happens.
    """
    messages = example["messages"]
    try:
        text = tokenizer.apply_chat_template(messages, tokenize=False)
    except Exception:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        merged = []
        folded = False
        for message in messages:
            if message["role"] == "system":
                continue
            if message["role"] == "user" and not folded:
                merged.append(
                    {"role": "user", "content": f"{system}\n\n{message['content']}"}
                )
                folded = True
            else:
                merged.append(message)
        text = tokenizer.apply_chat_template(merged, tokenize=False)
    return {"text": text}


def main() -> None:
    args = parse_args()
    hf_token = os.environ.get("HF_API_KEY") or None

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, token=hf_token)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    dataset = load_dataset(
        "json",
        data_files={
            "train": str(args.train_file),
            **({"eval": str(args.eval_file)} if args.eval_file.exists() else {}),
        },
    )
    dataset = dataset.map(
        lambda ex: format_example(ex, tokenizer),
        remove_columns=dataset["train"].column_names,
    )

    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=LORA_TARGET_MODULES,
        bias="none",
        task_type="CAUSAL_LM",
    )
    sft_config = SFTConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=args.warmup_ratio,
        optim="paged_adamw_8bit",
        bf16=True,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        max_grad_norm=0.3,
        weight_decay=0.01,
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch" if "eval" in dataset else "no",
        packing=True,
        max_seq_length=args.max_seq_length,
        dataset_text_field="text",
        seed=args.seed,
        report_to="none",
        model_init_kwargs={
            "quantization_config": quant_config,
            "device_map": "auto",
            "torch_dtype": torch.bfloat16,
            "token": hf_token,
        },
    )

    trainer = SFTTrainer(
        model=args.base_model,
        args=sft_config,
        train_dataset=dataset["train"],
        eval_dataset=dataset.get("eval"),
        peft_config=lora_config,
        processing_class=tokenizer,
    )

    trainer.train()
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    print(f"adapter saved to {args.output_dir}")

    if args.push:
        if not hf_token:
            raise SystemExit("--push requires the HF_API_KEY env var to be set")
        trainer.model.push_to_hub(args.hub_repo, token=hf_token, private=True)
        tokenizer.push_to_hub(args.hub_repo, token=hf_token)
        print(f"adapter pushed to hub repo {args.hub_repo}")


if __name__ == "__main__":
    main()
