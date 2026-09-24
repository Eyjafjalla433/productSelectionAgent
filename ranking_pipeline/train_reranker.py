"""Train a lightweight Qwen3-Reranker scoring head with aligned data.

The official rules allow local scoring logic and prompt tuning, while
full-parameter training of base foundational LLMs is out of scope. This script
therefore freezes the transformer trunk and trains only the final
classification/score parameters. By default it combines the public 200 gold
sessions with the synthetic 3,021 proxy sessions for distribution alignment.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

from ranking_pipeline.qwen_reranker import DEFAULT_MODEL, format_pair
from ranking_pipeline.training_data import (
    RerankTrainingExample,
    build_public_training_examples,
    build_synthetic_training_examples,
)
from ranking_pipeline.distribution_alignment import (
    build_aligned_training_examples,
    summarize_examples,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RETRIEVAL_ROOT = REPOSITORY_ROOT / "retrieval-and-reranking"
DATA_ROOT = RETRIEVAL_ROOT / "data"
DEFAULT_PUBLIC_SET = DATA_ROOT / "public_set.jsonl"
DEFAULT_CATALOG = DATA_ROOT / "catalog.jsonl"
DEFAULT_SYNTHETIC_SET = (
    REPOSITORY_ROOT / "synthetic-data-3021" / "data" / "synthetic_contract_matched_all_3021.jsonl"
)
DEFAULT_SYNTHETIC_TIERS = (
    REPOSITORY_ROOT / "synthetic-data-3021" / "data" / "synthetic_contract_matched_all_3021_tiers.jsonl"
)
DEFAULT_SYNTHETIC_PRODUCTS = (
    REPOSITORY_ROOT / "synthetic-data-3021" / "data" / "product_filter_inference_3021.csv"
)
CHECKPOINT_ROOT = REPOSITORY_ROOT / "ranking_pipeline" / "checkpoints"

LORA_R = 8
LORA_ALPHA = 16
LORA_DROPOUT = 0.05
LORA_TARGET_MODULES = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-strategy",
        choices=("public", "synthetic", "aligned"),
        default="aligned",
    )
    parser.add_argument("--public-set", type=Path, default=DEFAULT_PUBLIC_SET)
    parser.add_argument("--public-top50", type=Path, default=None)
    parser.add_argument("--synthetic-set", type=Path, default=DEFAULT_SYNTHETIC_SET)
    parser.add_argument("--synthetic-tiers", type=Path, default=DEFAULT_SYNTHETIC_TIERS)
    parser.add_argument("--synthetic-products", type=Path, default=DEFAULT_SYNTHETIC_PRODUCTS)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--base-model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--adapter-checkpoint",
        type=Path,
        default=None,
        help="Optional saved PEFT/LoRA checkpoint to continue training from.",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--log-interval", type=int, default=50, help="Print training progress every N steps; 0 disables")
    parser.add_argument(
        "--save-every-epoch",
        action="store_true",
        default=True,
        help="Save a checkpoint after every epoch (default)",
    )
    parser.add_argument(
        "--no-save-every-epoch",
        action="store_false",
        dest="save_every_epoch",
        help="Save only the best checkpoint after training finishes",
    )
    parser.add_argument(
        "--load-in-4bit",
        action="store_true",
        default=True,
        help="Load the base model with BitsAndBytes 4-bit quantization (default)",
    )
    parser.add_argument(
        "--no-load-in-4bit",
        action="store_false",
        dest="load_in_4bit",
        help="Disable 4-bit quantization and load the base model in full precision",
    )
    parser.add_argument("--negatives-per-positive", type=int, default=4, help="Public negatives per positive")
    parser.add_argument(
        "--loss",
        choices=("bce", "listwise"),
        default="bce",
        help="Training objective: pointwise BCE or listwise contrastive",
    )
    parser.add_argument(
        "--public-top-k",
        type=int,
        default=0,
        help="Use retrieval Top-K candidates as public negatives; 0 uses sampled negatives",
    )
    parser.add_argument(
        "--public-retrieval-mode",
        choices=("lite", "exact"),
        default="lite",
        help="Retrieval stage used for public Top-K candidate generation",
    )
    parser.add_argument("--synthetic-negatives-per-positive", type=int, default=8)
    parser.add_argument(
        "--synthetic-hard-negatives-per-positive",
        type=int,
        default=4,
        help="Number of negatives taken from retrieval Top-K before category sampling",
    )
    parser.add_argument(
        "--synthetic-retrieval-mode",
        choices=("lite", "exact"),
        default="lite",
        help="Retrieval stage used for hard-negative mining",
    )
    parser.add_argument("--public-positive-weight", type=float, default=5.0)
    parser.add_argument("--public-negative-weight", type=float, default=1.0)
    parser.add_argument("--synthetic-tier-filter", nargs="+", default=("high_confidence", "probable"))
    parser.add_argument("--eval-fraction", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _split_examples(examples: list[RerankTrainingExample], fraction: float, seed: int):
    # Keep one positive and its associated negatives together in the same split.
    positive_groups: dict[str, list[RerankTrainingExample]] = {}
    order: list[str] = []
    last_group: str | None = None
    for example in examples:
        if example.label == 1.0:
            group_id = str(len(order))
            order.append(group_id)
            positive_groups[group_id] = [example]
            last_group = group_id
        else:
            if last_group is None:
                continue
            if last_group not in positive_groups:
                positive_groups[last_group] = []
            positive_groups[last_group].append(example)
    random.Random(seed).shuffle(order)
    eval_count = max(1, int(len(order) * fraction))
    eval_groups = set(order[:eval_count])
    train: list[RerankTrainingExample] = []
    valid: list[RerankTrainingExample] = []
    for group_id in order:
        target = valid if group_id in eval_groups else train
        target.extend(positive_groups[group_id])
    return train, valid


def build_run_output(args: argparse.Namespace) -> Path:
    if args.output is not None:
        return args.output
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        CHECKPOINT_ROOT
        / (
            "0.6Blora"
            f"_{timestamp}"
            f"_ep{args.epochs}"
            f"_lr{args.learning_rate:g}"
        )
    )


def build_epoch_output(run_output: Path, epoch: int) -> Path:
    return run_output.with_name(run_output.name + f"_epoch{epoch}")


def build_best_output(run_output: Path, best_public_accuracy: float) -> Path:
    return run_output.with_name(
        run_output.name + f"_best_pubacc{best_public_accuracy:.3f}"
    )


def _save_checkpoint(model, tokenizer, output: Path, summary: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(output)
    model.save_pretrained(output)
    (output / "training_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved checkpoint to {output}")


def group_examples(examples: list[RerankTrainingExample]) -> list[list[RerankTrainingExample]]:
    groups: dict[str, list[RerankTrainingExample]] = {}
    order: list[str] = []
    for example in examples:
        group_id = example.group_id or f"{example.parent_asin}:{example.query}"
        if group_id not in groups:
            groups[group_id] = []
            order.append(group_id)
        groups[group_id].append(example)
    return [groups[group_id] for group_id in order]


def _encode_group(tokenizer, group: list[RerankTrainingExample], device: str, max_length: int):
    texts = [format_pair(example.query, example.document) for example in group]
    inputs = tokenizer(
        texts,
        return_tensors="pt",
        padding=True,
        truncation="longest_first",
        max_length=max_length,
    )
    return {name: value.to(device) for name, value in inputs.items()}


def _listwise_loss(logits, positive_indices: Sequence[int]):
    import torch

    log_probs = torch.log_softmax(logits, dim=0)
    return -log_probs[positive_indices].mean()


def _evaluate_groups(tokenizer, model, groups, device, max_length) -> float:
    import torch

    if not groups:
        return 0.0
    model.eval()
    correct = 0
    total = 0
    with torch.inference_mode():
        for group in groups:
            positive_indices = [index for index, example in enumerate(group) if example.label > 0.5]
            if not positive_indices:
                continue
            inputs = _encode_group(tokenizer, group, device, max_length)
            logits = model(**inputs).logits.squeeze(-1)
            prediction = int(torch.argmax(logits).item())
            correct += int(prediction in positive_indices)
            total += 1
    return correct / max(1, total)


def _run_listwise_training(
    args,
    summary: dict,
    model,
    tokenizer,
    device: str,
    train_examples: list[RerankTrainingExample],
    valid_examples: list[RerankTrainingExample],
    public_validation_examples: list[RerankTrainingExample],
    run_output: Path,
) -> None:
    import torch
    from peft import get_peft_model_state_dict, set_peft_model_state_dict

    train_groups = group_examples(train_examples)
    valid_groups = group_examples(valid_examples)
    public_groups = group_examples(public_validation_examples)
    optimizer = torch.optim.AdamW(
        (parameter for parameter in model.parameters() if parameter.requires_grad),
        lr=args.learning_rate,
    )
    best_public_accuracy = -1.0
    best_state_dict = None
    selected_epoch: int | None = None

    for epoch in range(1, args.epochs + 1):
        random.Random(args.seed + epoch).shuffle(train_groups)
        model.train()
        epoch_started_at = time.perf_counter()
        total_loss = 0.0
        total = 0
        correct = 0
        steps_in_epoch = len(train_groups)
        for step, group in enumerate(train_groups, start=1):
            positive_indices = [index for index, example in enumerate(group) if example.label > 0.5]
            if not positive_indices:
                continue
            step_started_at = time.perf_counter()
            optimizer.zero_grad(set_to_none=True)
            inputs = _encode_group(tokenizer, group, device, args.max_length)
            logits = model(**inputs).logits.squeeze(-1)
            loss = _listwise_loss(logits, positive_indices)
            group_weight = sum(group[index].weight for index in positive_indices) / len(positive_indices)
            loss = loss * group_weight
            loss.backward()
            optimizer.step()
            prediction = int(torch.argmax(logits).item())
            correct += int(prediction in positive_indices)
            total += 1
            total_loss += float(loss.item())
            if args.log_interval > 0 and step % args.log_interval == 0:
                now = time.perf_counter()
                avg_step_seconds = (now - epoch_started_at) / step
                remaining_steps = (args.epochs - epoch) * steps_in_epoch + (steps_in_epoch - step)
                print(
                    json.dumps(
                        {
                            "loss": "listwise",
                            "epoch": epoch,
                            "step": step,
                            "steps_in_epoch": steps_in_epoch,
                            "step_loss": round(float(loss.item()), 6),
                            "running_accuracy": round(correct / max(1, total), 6),
                            "last_step_seconds": round(now - step_started_at, 3),
                            "avg_step_seconds": round(avg_step_seconds, 3),
                            "eta_seconds": round(remaining_steps * avg_step_seconds, 1),
                        }
                    ),
                    flush=True,
                )
        train_accuracy = correct / max(1, total)
        valid_accuracy = _evaluate_groups(tokenizer, model, valid_groups, device, args.max_length)
        public_accuracy = _evaluate_groups(tokenizer, model, public_groups, device, args.max_length)
        if public_groups and public_accuracy > best_public_accuracy:
            best_public_accuracy = public_accuracy
            best_state_dict = {
                name: tensor.detach().cpu().clone()
                for name, tensor in get_peft_model_state_dict(model).items()
            }
            selected_epoch = epoch
        print(
            json.dumps(
                {
                    "loss": "listwise",
                    "epoch": epoch,
                    "train_loss": round(total_loss / max(1, total), 6),
                    "train_accuracy": round(train_accuracy, 6),
                    "valid_accuracy": round(valid_accuracy, 6),
                    "public_accuracy": round(public_accuracy, 6),
                    "epoch_seconds": round(time.perf_counter() - epoch_started_at, 1),
                }
            ),
            flush=True,
        )
        if args.save_every_epoch:
            epoch_summary = dict(summary)
            epoch_summary["loss"] = "listwise"
            epoch_summary["best_public_accuracy"] = round(best_public_accuracy, 6)
            epoch_summary["selected_epoch"] = selected_epoch
            _save_checkpoint(
                model,
                tokenizer,
                build_epoch_output(run_output, epoch),
                epoch_summary,
            )

    if best_state_dict is not None:
        set_peft_model_state_dict(model, best_state_dict)
    summary["loss"] = "listwise"
    summary["best_public_accuracy"] = round(best_public_accuracy, 6)
    summary["selected_epoch"] = selected_epoch
    _save_checkpoint(
        model,
        tokenizer,
        build_best_output(run_output, best_public_accuracy),
        summary,
    )


def _load_peft_model(
    model_name: str,
    device: str,
    *,
    load_in_4bit: bool = True,
    adapter_checkpoint: Path | None = None,
):
    import torch
    from peft import (
        LoraConfig,
        PeftModel,
        TaskType,
        get_peft_model,
        prepare_model_for_kbit_training,
    )
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name, padding_side="left")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model_kwargs: dict = {}
    if load_in_4bit:
        if not device.startswith("cuda"):
            raise RuntimeError(
                "--load-in-4bit requires a CUDA device. Use --no-load-in-4bit for CPU."
            )
        from transformers import BitsAndBytesConfig

        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        model_kwargs["device_map"] = "auto"
    model = AutoModelForSequenceClassification.from_pretrained(model_name, **model_kwargs)
    if load_in_4bit:
        model = prepare_model_for_kbit_training(model)
    lora_config = LoraConfig(
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        task_type=TaskType.SEQ_CLS,
        target_modules=list(LORA_TARGET_MODULES),
        modules_to_save=["score"],
    )
    if adapter_checkpoint is not None:
        adapter_checkpoint = Path(adapter_checkpoint)
        if not adapter_checkpoint.is_dir():
            raise ValueError(
                f"--adapter-checkpoint must be a directory: {adapter_checkpoint}"
            )
        model = PeftModel.from_pretrained(
            model,
            str(adapter_checkpoint),
            is_trainable=True,
        )
    else:
        model = get_peft_model(model, lora_config)
    model.to(device)
    trainable_count = sum(
        parameter.numel() for parameter in model.parameters() if parameter.requires_grad
    )
    model.train()
    return tokenizer, model, trainable_count


def _encode_batch(tokenizer, examples: list[RerankTrainingExample], device: str, max_length: int):
    texts = [format_pair(example.query, example.document) for example in examples]
    inputs = tokenizer(
        texts,
        return_tensors="pt",
        padding=True,
        truncation="longest_first",
        max_length=max_length,
    )
    labels = [example.label for example in examples]
    weights = [example.weight for example in examples]
    return (
        {name: value.to(device) for name, value in inputs.items()},
        labels,
        weights,
    )


def train(args: argparse.Namespace) -> None:
    import torch
    from torch import nn
    from peft import get_peft_model_state_dict, set_peft_model_state_dict

    if args.epochs is None:
        args.epochs = 1 if args.data_strategy == "public" else 2
    if args.loss == "listwise" and args.public_top_k == 0:
        args.public_top_k = 20

    if args.data_strategy == "public":
        examples = build_public_training_examples(
            args.public_set,
            args.public_top50,
            args.catalog,
            negatives_per_positive=args.negatives_per_positive,
            public_top_k=args.public_top_k,
            public_retrieval_mode=args.public_retrieval_mode,
            negative_pool_csv_path=args.synthetic_products,
            seed=args.seed,
            limit=args.limit,
            positive_weight=args.public_positive_weight,
            negative_weight=args.public_negative_weight,
        )
    elif args.data_strategy == "synthetic":
        examples = build_synthetic_training_examples(
            args.synthetic_set,
            args.catalog,
            product_csv_path=args.synthetic_products,
            tiers_path=args.synthetic_tiers,
            negatives_per_positive=args.synthetic_negatives_per_positive,
            hard_negatives_per_positive=args.synthetic_hard_negatives_per_positive,
            retrieval_mode=args.synthetic_retrieval_mode,
            seed=args.seed,
            limit=args.limit,
            tier_filter=tuple(args.synthetic_tier_filter),
        )
    else:
        examples = build_aligned_training_examples(
            args.public_set,
            args.public_top50,
            args.synthetic_set,
            args.catalog,
            synthetic_product_csv_path=args.synthetic_products,
            synthetic_tiers_path=args.synthetic_tiers,
            public_negatives_per_positive=args.negatives_per_positive,
            synthetic_negatives_per_positive=args.synthetic_negatives_per_positive,
            public_positive_weight=args.public_positive_weight,
            public_negative_weight=args.public_negative_weight,
            synthetic_tier_filter=tuple(args.synthetic_tier_filter),
            synthetic_hard_negatives_per_positive=args.synthetic_hard_negatives_per_positive,
            synthetic_retrieval_mode=args.synthetic_retrieval_mode,
            public_top_k=args.public_top_k,
            public_retrieval_mode=args.public_retrieval_mode,
            seed=args.seed,
            limit=args.limit,
        )
    if not examples:
        raise RuntimeError("No training examples could be built")
    public_validation_examples: list[RerankTrainingExample] = []
    if args.data_strategy in {"public", "aligned"}:
        public_validation_examples = build_public_training_examples(
            args.public_set,
            args.public_top50,
            args.catalog,
            negatives_per_positive=args.negatives_per_positive,
            public_top_k=args.public_top_k,
            public_retrieval_mode=args.public_retrieval_mode,
            negative_pool_csv_path=args.synthetic_products,
            seed=args.seed,
            positive_weight=1.0,
            negative_weight=1.0,
        )
    train_examples, valid_examples = _split_examples(examples, args.eval_fraction, args.seed)
    distribution = summarize_examples(examples)
    summary = {
        "data_strategy": args.data_strategy,
        "positive_examples": sum(example.label == 1.0 for example in examples),
        "negative_examples": sum(example.label == 0.0 for example in examples),
        "train_examples": len(train_examples),
        "valid_examples": len(valid_examples),
        "public_validation_examples": len(public_validation_examples),
        "trainable_parameters": None,
        "best_public_accuracy": None,
        "selected_epoch": None,
        "distribution": distribution.__dict__,
    }
    print(json.dumps(summary, indent=2))
    if args.dry_run:
        return

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer, model, trainable_count = _load_peft_model(
        args.base_model,
        device,
        load_in_4bit=args.load_in_4bit,
        adapter_checkpoint=args.adapter_checkpoint,
    )
    summary["trainable_parameters"] = trainable_count
    summary["adapter_checkpoint"] = str(args.adapter_checkpoint)
    summary["lora"] = {
        "r": LORA_R,
        "lora_alpha": LORA_ALPHA,
        "lora_dropout": LORA_DROPOUT,
        "target_modules": list(LORA_TARGET_MODULES),
        "modules_to_save": ["score"],
    }
    print(json.dumps({"trainable_parameters": trainable_count, "device": device}))
    run_output = build_run_output(args)
    if args.loss == "listwise":
        _run_listwise_training(
            args,
            summary,
            model,
            tokenizer,
            device,
            train_examples,
            valid_examples,
            public_validation_examples,
            run_output,
        )
        return

    optimizer = torch.optim.AdamW(
        (parameter for parameter in model.parameters() if parameter.requires_grad),
        lr=args.learning_rate,
    )
    loss_fn = nn.BCEWithLogitsLoss(reduction="none")
    best_public_accuracy = -1.0
    best_state_dict = None
    selected_epoch: int | None = None

    for epoch in range(1, args.epochs + 1):
        random.Random(args.seed + epoch).shuffle(train_examples)
        total_loss = 0.0
        total = 0
        correct = 0
        model.train()
        epoch_started_at = time.perf_counter()
        steps_in_epoch = (len(train_examples) + args.batch_size - 1) // args.batch_size
        for start in range(0, len(train_examples), args.batch_size):
            step = start // args.batch_size + 1
            step_started_at = time.perf_counter()
            batch = train_examples[start : start + args.batch_size]
            inputs, labels, weights = _encode_batch(tokenizer, batch, device, args.max_length)
            labels_tensor = torch.tensor(labels, dtype=torch.float32, device=device)
            weights_tensor = torch.tensor(weights, dtype=torch.float32, device=device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(**inputs).logits.squeeze(-1)
            elementwise_loss = loss_fn(logits, labels_tensor)
            loss = (elementwise_loss * weights_tensor).mean()
            loss.backward()
            optimizer.step()
            predictions = (torch.sigmoid(logits) >= 0.5).float()
            correct += int((predictions == labels_tensor).sum().item())
            total += len(batch)
            total_loss += float(loss.item()) * len(batch)
            if args.log_interval > 0 and step % args.log_interval == 0:
                now = time.perf_counter()
                last_step_seconds = now - step_started_at
                epoch_elapsed = now - epoch_started_at
                avg_step_seconds = epoch_elapsed / step
                remaining_steps = (
                    (args.epochs - epoch) * steps_in_epoch
                    + (steps_in_epoch - step)
                )
                estimated_total_seconds = (
                    args.epochs * steps_in_epoch * avg_step_seconds
                )
                print(
                    json.dumps(
                        {
                            "epoch": epoch,
                            "step": step,
                            "steps_in_epoch": steps_in_epoch,
                            "step_loss": round(float(loss.item()), 6),
                            "running_accuracy": round(correct / max(1, total), 6),
                            "last_step_seconds": round(last_step_seconds, 3),
                            "avg_step_seconds": round(avg_step_seconds, 3),
                            "eta_seconds": round(remaining_steps * avg_step_seconds, 1),
                            "estimated_total_seconds": round(estimated_total_seconds, 1),
                        }
                    ),
                    flush=True,
                )
        train_accuracy = correct / max(1, total)
        valid_accuracy = evaluate(
            tokenizer, model, valid_examples, device, args.max_length, args.batch_size
        )
        public_accuracy = evaluate(
            tokenizer, model, public_validation_examples, device, args.max_length, args.batch_size
        )
        if public_validation_examples and public_accuracy > best_public_accuracy:
            best_public_accuracy = public_accuracy
            best_state_dict = {
                name: tensor.detach().cpu().clone()
                for name, tensor in get_peft_model_state_dict(model).items()
            }
            selected_epoch = epoch
        print(
            json.dumps(
                {
                    "epoch": epoch,
                    "train_loss": round(total_loss / max(1, total), 6),
                    "train_accuracy": round(train_accuracy, 6),
                    "valid_accuracy": round(valid_accuracy, 6),
                    "public_accuracy": round(public_accuracy, 6),
                    "epoch_seconds": round(time.perf_counter() - epoch_started_at, 1),
                }
            ),
            flush=True,
        )
        if args.save_every_epoch:
            epoch_summary = dict(summary)
            epoch_summary["best_public_accuracy"] = round(best_public_accuracy, 6)
            epoch_summary["selected_epoch"] = selected_epoch
            _save_checkpoint(
                model,
                tokenizer,
                build_epoch_output(run_output, epoch),
                epoch_summary,
            )

    if best_state_dict is not None:
        set_peft_model_state_dict(model, best_state_dict)
    summary["best_public_accuracy"] = round(best_public_accuracy, 6)
    summary["selected_epoch"] = selected_epoch

    _save_checkpoint(
        model,
        tokenizer,
        build_best_output(run_output, best_public_accuracy),
        summary,
    )


def evaluate(tokenizer, model, examples, device, max_length, batch_size) -> float:
    import torch

    if not examples:
        return 0.0
    model.eval()
    correct = 0
    total = 0
    with torch.inference_mode():
        for start in range(0, len(examples), batch_size):
            batch = examples[start : start + batch_size]
            inputs, labels, _ = _encode_batch(tokenizer, batch, device, max_length)
            labels_tensor = torch.tensor(labels, dtype=torch.float32, device=device)
            logits = model(**inputs).logits.squeeze(-1)
            predictions = (torch.sigmoid(logits) >= 0.5).float()
            correct += int((predictions == labels_tensor).sum().item())
            total += len(batch)
    return correct / max(1, total)


if __name__ == "__main__":
    train(parse_args())
