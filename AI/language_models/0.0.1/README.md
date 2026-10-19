# Basic Language Model v0.0.1

A minimal, self-contained, CPU-friendly language model written in pure Python +
numpy. Designed to run on older hardware (e.g. HP P7-1240 / Radeon 7660D) with
no GPU and no heavy deep-learning frameworks.

## Architecture

Tiny decoder-only **Transformer** (GPT-style):

* Token embeddings + positional embeddings
* Causal multi-head self-attention (with full backpropagation)
* Feed-forward blocks with residual connections + LayerNorm
* Trained with cross-entropy loss and SGD

The architecture is the same family as large language models; scale up
`vocab_size`, `dim`, `num_heads`, `num_layers`, `block_size`, and training data
to grow toward a larger LLM.

## Self-Healing (safe, constrained)

`basic_llm.py` includes a `SelfHealer` that can attempt to repair **only its own
source file** if a runtime problem occurs:

* A `.bak` backup is made before any edit.
* The edited file is validated with `compile()` before being accepted.
* On validation failure, the backup is restored.
* A hard cap limits repair attempts.
* No other file is ever read or written by the self-healer.

## Files

| File | Purpose |
|------|---------|
| `basic_llm.py` | Main model + self-healer (self-editable) |
| `train.py` | Training wrapper |
| `generate.py` | Generation wrapper |
| `model.json` | Saved model weights (created after training) |
| `requirements.txt` | Python dependencies |
| `README.md` | This file |

## Requirements

* Python 3.8+
* numpy

Install:

```bash
pip install --break-system-packages -r requirements.txt
```

## Usage

Train (on the built-in corpus):

```bash
python3 basic_llm.py train --iters 500
```

Generate text:

```bash
python3 basic_llm.py gen --prompt "Hello" --tokens 200
```

Demo (train then generate):

```bash
python3 basic_llm.py demo --prompt "Hello" --iters 300
```

Or use the wrappers:

```bash
python3 train.py
python3 generate.py --prompt "The quick"
```

## Scaling to a larger model

Edit `basic_llm.py` and increase the model dimensions in `main()` or the
wrapper scripts:

```python
model = BasicLLM(
    vocab_size=len(stoi),
    dim=256,        # wider
    num_heads=8,
    num_layers=12,  # deeper
    block_size=256, # longer context
    lr=1e-3,
)
```

More data and longer training will produce better results. Because this is pure
numpy on CPU, keep dimensions modest for reasonable speed on older hardware.

## License

MIT