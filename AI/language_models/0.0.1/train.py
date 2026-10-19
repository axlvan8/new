#!/usr/bin/env python3
"""
train.py — convenience wrapper to train basic_llm.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from basic_llm import BasicLLM, train, build_vocab, encode, BUILTIN_TEXT
import numpy as np


def main():
    stoi, itos = build_vocab(BUILTIN_TEXT)
    data = np.array(encode(BUILTIN_TEXT, stoi), dtype=np.int64)

    model = BasicLLM(
        vocab_size=len(stoi), dim=32, num_heads=4,
        num_layers=2, block_size=32, lr=1e-2,
    )
    train(model, data, iters=500, batch_size=16, save_path="model.json")


if __name__ == "__main__":
    main()
