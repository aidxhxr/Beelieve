"""Merge the LoRA adapter into the base model and run a held-out evaluation.

Metrics:
- format compliance: share of generations parseable by ``app.parse`` (the
  exact parser used in production) with 1-3 well-formed recommendations;
- ROUGE-L F1 of generations against the reference recommendations (pure-python
  LCS implementation, no extra dependency).

Usage (from services/recommender):
    python -m finetune.merge_and_eval \
        --adapter finetune/out/beelieve-advisor-lora \
        --merged-out finetune/out/beelieve-advisor-merged \
        --eval-file finetune/data/eval.jsonl --limit 50
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parse import parse_recommendations
from app.prompts import MAX_RECOMMENDATIONS

DEFAULT_BASE_MODEL = os.environ.get("HF_BASE_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def rouge_l_f1(candidate: str, reference: str) -> float:
    """ROUGE-L F1 via longest common subsequence over word tokens."""
    cand, ref = _tokenize(candidate), _tokenize(reference)
    if not cand or not ref:
        return 0.0
    # O(len(cand) * len(ref)) DP with two rows.
    previous = [0] * (len(ref) + 1)
    for c_tok in cand:
        current = [0] * (len(ref) + 1)
        for j, r_tok in enumerate(ref, start=1):
            if c_tok == r_tok:
                current[j] = previous[j - 1] + 1
            else:
                current[j] = max(previous[j], current[j - 1])
        previous = current
    lcs = previous[-1]
    precision = lcs / len(cand)
    recall = lcs / len(ref)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def is_format_compliant(text: str) -> bool:
    recs = parse_recommendations(text)
    return (
        1 <= len(recs) <= MAX_RECOMMENDATIONS
        and all(rec.title and rec.body and 1 <= rec.priority <= 5 for rec in recs)
    )


def load_merged_model(
    base_model: str, adapter: Path, merged_out: Path, hf_token: str | None
) -> tuple[AutoModelForCausalLM, AutoTokenizer]:
    tokenizer = AutoTokenizer.from_pretrained(adapter, token=hf_token)
    base = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        token=hf_token,
    )
    model = PeftModel.from_pretrained(base, str(adapter))
    model = model.merge_and_unload()
    merged_out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(merged_out), safe_serialization=True)
    tokenizer.save_pretrained(str(merged_out))
    print(f"merged model saved to {merged_out}")
    return model, tokenizer


def generate(
    model: AutoModelForCausalLM,
    tokenizer: AutoTokenizer,
    messages: list[dict[str, str]],
    max_new_tokens: int,
) -> str:
    try:
        prompt_ids = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt"
        )
    except Exception:
        # Fold system into first user turn for templates without system support.
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        rest = [m for m in messages if m["role"] != "system"]
        if rest and rest[0]["role"] == "user":
            rest[0] = {"role": "user", "content": f"{system}\n\n{rest[0]['content']}"}
        prompt_ids = tokenizer.apply_chat_template(
            rest, add_generation_prompt=True, return_tensors="pt"
        )
    prompt_ids = prompt_ids.to(model.device)
    with torch.no_grad():
        output = model.generate(
            prompt_ids,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            temperature=None,
            top_p=None,
            pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
        )
    return tokenizer.decode(output[0][prompt_ids.shape[1]:], skip_special_tokens=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument(
        "--adapter", type=Path,
        default=Path(__file__).parent / "out" / "beelieve-advisor-lora",
    )
    parser.add_argument(
        "--merged-out", type=Path,
        default=Path(__file__).parent / "out" / "beelieve-advisor-merged",
    )
    parser.add_argument(
        "--eval-file", type=Path,
        default=Path(__file__).parent / "data" / "eval.jsonl",
    )
    parser.add_argument(
        "--report", type=Path,
        default=Path(__file__).parent / "out" / "eval_report.json",
    )
    parser.add_argument("--limit", type=int, default=50, help="max eval examples")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    args = parser.parse_args()

    hf_token = os.environ.get("HF_API_KEY") or None
    model, tokenizer = load_merged_model(
        args.base_model, args.adapter, args.merged_out, hf_token
    )
    model.eval()

    with args.eval_file.open("r", encoding="utf-8") as fh:
        examples = [json.loads(line) for line in fh if line.strip()][: args.limit]
    if not examples:
        raise SystemExit(f"no eval examples in {args.eval_file}")

    per_example: list[dict[str, object]] = []
    for i, example in enumerate(examples):
        messages = example["messages"]
        reference = messages[-1]["content"]
        prompt_messages = messages[:-1]
        candidate = generate(model, tokenizer, prompt_messages, args.max_new_tokens)
        compliant = is_format_compliant(candidate)
        rouge = rouge_l_f1(candidate, reference)
        per_example.append(
            {
                "index": i,
                "lang": example.get("meta", {}).get("lang"),
                "topic": example.get("meta", {}).get("topic"),
                "format_compliant": compliant,
                "rouge_l_f1": round(rouge, 4),
                "n_parsed": len(parse_recommendations(candidate)),
            }
        )
        print(
            f"[{i + 1}/{len(examples)}] compliant={compliant} rougeL={rouge:.3f}"
        )

    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "base_model": args.base_model,
        "adapter": str(args.adapter),
        "n_examples": len(per_example),
        "format_compliance": round(
            mean(1.0 if row["format_compliant"] else 0.0 for row in per_example), 4
        ),
        "rouge_l_f1_mean": round(
            mean(float(row["rouge_l_f1"]) for row in per_example), 4
        ),
        "examples": per_example,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"eval report -> {args.report}\n"
        f"format compliance: {report['format_compliance']:.1%} | "
        f"ROUGE-L F1: {report['rouge_l_f1_mean']:.3f}"
    )


if __name__ == "__main__":
    main()
