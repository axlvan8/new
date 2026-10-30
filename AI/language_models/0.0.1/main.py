#!/usr/bin/env python3
"""
main.py — entry point for the Basic Language Model v0.0.1

Provides a simple menu and command-line interface to train and generate text
with the model in basic_llm.py.

The AI calculates its own max training iterations and max new tokens based on
the model size, vocabulary, data length, and available CPU cores. You can
override these with --iters and --tokens if desired.

Usage:
    python3 main.py              # interactive menu (auto-calculates iters/tokens)
    python3 main.py train        # train on built-in corpus (auto iters)
    python3 main.py gen          # generate text (auto tokens)
    python3 main.py demo         # train briefly, then generate (auto both)

Options:
    --prompt TEXT    generation prompt (default: "Hello")
    --iters N        training iterations (auto-calculated if omitted)
    --tokens N       max new tokens to generate (auto-calculated if omitted)
    --save PATH      model save/load path (default: model.json)
"""

import os
import sys

# Ensure we can import basic_llm from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from basic_llm import (
    BasicLLM,
    train,
    build_vocab,
    encode,
    decode,
    BUILTIN_TEXT,
)
import numpy as np

DEFAULT_SAVE = "model.json"
DEFAULT_DIMS = dict(dim=16, num_heads=4, num_layers=1, block_size=32, lr=1e-2)


def build_model(vocab_size: int) -> BasicLLM:
    return BasicLLM(vocab_size=vocab_size, **DEFAULT_DIMS)


def count_tokens(text: str, stoi: dict) -> int:
    """Return the number of tokens in a text string."""
    return len(encode(text, stoi))


def calc_max_iters(vocab_size: int, data_len: int, dim: int,
                   num_layers: int, cpu_count: int) -> int:
    """
    Automatically calculate a reasonable max training iteration count.

    Heuristics tuned for an older CPU (keep it light):
      * More unique tokens  -> more iterations needed to cover the space
      * More training data  -> more iterations to see it all
      * Bigger model        -> more iterations to converge
      * More CPU cores      -> can afford slightly more
    Results are clamped to a safe min/max so it never runs too long.
    """
    base = 300
    data_factor = min(data_len // 40, 700)        # up to +700
    vocab_factor = min(vocab_size * 3, 400)       # up to +400
    model_factor = (dim * num_layers)             # e.g. 64*2 = 128
    cpu_factor = min(cpu_count, 4) * 50           # up to +200

    iters = base + data_factor + vocab_factor + model_factor + cpu_factor

    # Clamp to reasonable bounds for an older PC
    return max(200, min(iters, 3000))


def calc_max_tokens(block_size: int, vocab_size: int, data_len: int) -> int:
    """
    Automatically calculate a reasonable max new-token generation count.

    Heuristics:
      * Larger context window -> can generate more coherently
      * Bigger vocab / more data -> slightly longer generations
    Clamped so generation stays fast on older hardware.
    """
    base = 50
    context_factor = block_size              # e.g. 32
    data_factor = min(data_len // 100, 100)  # up to +100
    vocab_factor = min(vocab_size, 50)       # up to +50

    tokens = base + context_factor + data_factor + vocab_factor

    return max(25, min(tokens, 500))


def prepare_data(data_file: str = None):
    if data_file:
        if not os.path.exists(data_file):
            print(f"[ERROR] data file not found: {data_file}")
            sys.exit(1)
        with open(data_file, "r", encoding="utf-8") as f:
            text = f.read()
        print(f"[DATA] loaded {len(text)} characters from {data_file}")
    else:
        text = BUILTIN_TEXT
        print(f"[DATA] using built-in corpus ({len(text)} characters)")
    stoi, itos = build_vocab(text)
    data = np.array(encode(text, stoi), dtype=np.int64)
    return stoi, itos, data


def print_token_stats(stoi, itos, data, prompt_ids=None, out_ids=None):
    """Display token calculations for the user."""
    print("\n=== Token Count ===")
    print(f"Vocabulary size (unique tokens): {len(stoi)}")
    print(f"Training data tokens:          {len(data)}")
    if prompt_ids is not None:
        print(f"Input prompt tokens:           {len(prompt_ids[0])}")
    if out_ids is not None:
        generated = len(out_ids[0]) - (len(prompt_ids[0]) if prompt_ids is not None else 0)
        print(f"Generated tokens:              {generated}")
        print(f"Total output tokens:           {len(out_ids[0])}")
    print("===================\n")


def cmd_train(args, stoi, itos, data):
    cpu_count = os.cpu_count() or 1
    model = build_model(len(stoi))
    iters = args.iters if args.iters is not None \
        else calc_max_iters(len(stoi), len(data), model.dim,
                            model.num_layers, cpu_count)
    print(f"[AUTO] Calculated max training iterations: {iters} "
          f"(vocab={len(stoi)}, data={len(data)}, CPU={cpu_count})")
    train(model, data, iters=iters, batch_size=16, save_path=args.save)
    print_token_stats(stoi, itos, data)
    print("[TRAIN] complete. Model saved to", args.save)


def cmd_gen(args, stoi, itos, data):
    model = build_model(len(stoi))
    if os.path.exists(args.save):
        try:
            model.load(args.save)
            # Validate dimensions match
            if (model.dim != DEFAULT_DIMS["dim"]
                    or model.num_layers != DEFAULT_DIMS["num_layers"]
                    or model.block_size != DEFAULT_DIMS["block_size"]):
                print(f"[GEN] saved model dims don't match current config; using untrained weights.")
                model = build_model(len(stoi))
        except Exception as e:
            print(f"[GEN] could not load saved model ({e}); using untrained weights.")
            model = build_model(len(stoi))
    else:
        print(f"[GEN] no saved model at {args.save}; using untrained weights.")

    context = np.array([encode(args.prompt, stoi)], dtype=np.int64)
    tokens = args.tokens if args.tokens is not None \
        else calc_max_tokens(model.block_size, len(stoi), len(data))
    print(f"[AUTO] Calculated max new tokens: {tokens} "
          f"(block_size={model.block_size}, vocab={len(stoi)}, data={len(data)})")
    print(f"[AUTO] Sampling temperature: {args.temp}, "
          f"top-k: {args.top_k}, rep-penalty: {args.rep_penalty}")
    out = model.generate(context, max_new_tokens=tokens, temperature=args.temp,
                         top_k=args.top_k, repetition_penalty=args.rep_penalty)
    print("\n[GEN]", decode(out[0].tolist(), itos), "\n")
    print_token_stats(stoi, itos, data, prompt_ids=context, out_ids=out)


def cmd_demo(args, stoi, itos, data):
    cpu_count = os.cpu_count() or 1
    model = build_model(len(stoi))
    iters = args.iters if args.iters is not None \
        else calc_max_iters(len(stoi), len(data), model.dim,
                            model.num_layers, cpu_count)
    print(f"[AUTO] Calculated max training iterations: {iters} "
          f"(vocab={len(stoi)}, data={len(data)}, CPU={cpu_count})")
    train(model, data, iters=iters, batch_size=16, save_path=args.save)

    context = np.array([encode(args.prompt, stoi)], dtype=np.int64)
    tokens = args.tokens if args.tokens is not None \
        else calc_max_tokens(model.block_size, len(stoi), len(data))
    print(f"[AUTO] Calculated max new tokens: {tokens} "
          f"(block_size={model.block_size}, vocab={len(stoi)}, data={len(data)})")
    print(f"[AUTO] Sampling temperature: {args.temp}, "
          f"top-k: {args.top_k}, rep-penalty: {args.rep_penalty}")
    out = model.generate(context, max_new_tokens=tokens, temperature=args.temp,
                         top_k=args.top_k, repetition_penalty=args.rep_penalty)
    print("\n[DEMO]", decode(out[0].tolist(), itos), "\n")
    print_token_stats(stoi, itos, data, prompt_ids=context, out_ids=out)


def interactive_menu(stoi, itos, data, data_file=None):
    cpu_count = os.cpu_count() or 1
    model = build_model(len(stoi))
    auto_iters = calc_max_iters(len(stoi), len(data), model.dim,
                                model.num_layers, cpu_count)
    auto_tokens = calc_max_tokens(model.block_size, len(stoi), len(data))

    while True:
        source = f" ({data_file})" if data_file else " (built-in)"
        print(f"\n=== Basic LLM v0.0.1{source} ===")
        print(f"1. Train model          (auto iters: {auto_iters})")
        print(f"2. Generate text        (auto tokens: {auto_tokens})")
        print(f"3. Demo (train + gen)   (auto both)")
        print("4. Exit")
        choice = input("Select an option [1-4]: ").strip()

        if choice == "1":
            iters_in = input(f"Training iterations (default {auto_iters}): ").strip()
            iters = int(iters_in) if iters_in.isdigit() else auto_iters
            save = input(f"Save path (default {DEFAULT_SAVE}): ").strip() or DEFAULT_SAVE
            class A:
                pass
            a = A(); a.iters = iters; a.save = save; a.data_file = data_file
            cmd_train(a, stoi, itos, data)

        elif choice == "2":
            prompt = input("Prompt (default 'Hello'): ").strip() or "Hello"
            tokens_in = input(f"Max new tokens (default {auto_tokens}): ").strip()
            tokens = int(tokens_in) if tokens_in.isdigit() else auto_tokens
            temp_in = input("Sampling temperature, 0.5-2.0 (default 1.0): ").strip()
            try:
                temp = float(temp_in)
            except ValueError:
                temp = 1.0
            topk_in = input("Top-k sampling, 0=disabled (default 0): ").strip()
            top_k = int(topk_in) if topk_in.isdigit() else 0
            rep_in = input("Repetition penalty, 1.0-2.0 (default 1.2): ").strip()
            try:
                rep_penalty = float(rep_in)
            except ValueError:
                rep_penalty = 1.2
            save = input(f"Load path (default {DEFAULT_SAVE}): ").strip() or DEFAULT_SAVE
            class A:
                pass
            a = A(); a.prompt = prompt; a.tokens = tokens; a.temp = temp
            a.top_k = top_k; a.rep_penalty = rep_penalty; a.save = save
            cmd_gen(a, stoi, itos, data)

        elif choice == "3":
            iters_in = input(f"Training iterations (default {auto_iters}): ").strip()
            iters = int(iters_in) if iters_in.isdigit() else auto_iters
            prompt = input("Prompt (default 'Hello'): ").strip() or "Hello"
            tokens_in = input(f"Max new tokens (default {auto_tokens}): ").strip()
            tokens = int(tokens_in) if tokens_in.isdigit() else auto_tokens
            temp_in = input("Sampling temperature, 0.5-2.0 (default 1.0): ").strip()
            try:
                temp = float(temp_in)
            except ValueError:
                temp = 1.0
            topk_in = input("Top-k sampling, 0=disabled (default 0): ").strip()
            top_k = int(topk_in) if topk_in.isdigit() else 0
            rep_in = input("Repetition penalty, 1.0-2.0 (default 1.2): ").strip()
            try:
                rep_penalty = float(rep_in)
            except ValueError:
                rep_penalty = 1.2
            save = input(f"Save/load path (default {DEFAULT_SAVE}): ").strip() or DEFAULT_SAVE
            class A:
                pass
            a = A(); a.iters = iters; a.prompt = prompt; a.tokens = tokens; a.temp = temp
            a.top_k = top_k; a.rep_penalty = rep_penalty; a.save = save
            cmd_demo(a, stoi, itos, data)

        elif choice == "4":
            print("Goodbye!")
            break

        else:
            print("Invalid option, try again.")


def main():
    import argparse
    p = argparse.ArgumentParser(description="Basic LLM v0.0.1 runner")
    p.add_argument("action", nargs="?", choices=["train", "gen", "demo"],
                   help="Action to perform (omits interactive menu)")
    p.add_argument("--prompt", type=str, default="Hello", help="Generation prompt")
    p.add_argument("--iters", type=int, default=None,
                   help="Training iterations (auto-calculated if omitted)")
    p.add_argument("--tokens", type=int, default=None,
                   help="Max new tokens (auto-calculated if omitted)")
    p.add_argument("--temp", type=float, default=1.0,
                   help="Sampling temperature (default 1.0; higher = more random)")
    p.add_argument("--top-k", type=int, default=0,
                   help="Top-k sampling (0 = disabled; limits to k most likely chars)")
    p.add_argument("--rep-penalty", type=float, default=1.2,
                   help="Repetition penalty (default 1.2; >1 discourages repeated chars)")
    p.add_argument("--data-file", type=str, default=None,
                   help="Local text file to train on instead of built-in corpus")
    p.add_argument("--save", type=str, default=DEFAULT_SAVE, help="Model path")
    args = p.parse_args()

    stoi, itos, data = prepare_data(args.data_file)

    if args.action == "train":
        cmd_train(args, stoi, itos, data)
    elif args.action == "gen":
        cmd_gen(args, stoi, itos, data)
    elif args.action == "demo":
        cmd_demo(args, stoi, itos, data)
    else:
        interactive_menu(stoi, itos, data, args.data_file)


if __name__ == "__main__":
    main()
