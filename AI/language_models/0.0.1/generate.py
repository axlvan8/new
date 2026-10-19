#!/usr/bin/env python3
"""
generate.py — convenience wrapper to generate text with basic_llm.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from basic_llm import BasicLLM, build_vocab, encode, decode, BUILTIN_TEXT
import numpy as np


def main():
    import argparse
    p = argparse.ArgumentParser(description="Generate text with Basic LLM")
    p.add_argument("--prompt", type=str, default="Hello")
    p.add_argument("--tokens", type=int, default=200)
    p.add_argument("--load", type=str, default="model.json")
    args = p.parse_args()

    stoi, itos = build_vocab(BUILTIN_TEXT)
    model = BasicLLM(vocab_size=len(stoi), dim=32, num_heads=4,
                     num_layers=2, block_size=32, lr=1e-2)

    if os.path.exists(args.load):
        model.load(args.load)
    else:
        print(f"[gen] no saved model at {args.load}; using untrained weights.")

    context = np.array([encode(args.prompt, stoi)], dtype=np.int64)
    out = model.generate(context, max_new_tokens=args.tokens)
    print(decode(out[0].tolist(), itos))


if __name__ == "__main__":
    main()
