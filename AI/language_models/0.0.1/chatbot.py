#!/usr/bin/env python3
"""
chatbot.py — Hybrid neural + Wikipedia chatbot v0.0.1

A CPU-friendly chatbot for older hardware (HP P7-1240 / Radeon 7660D).

Architecture:
  * Tiny character-level Transformer (from basic_llm.py) for conversational
    generation — runs on numpy, no GPU, no heavy frameworks.
  * Wikipedia API lookup for factual questions — reads only, never writes
    to the web. Falls back to neural generation when Wikipedia has nothing.

Usage:
    python3 chatbot.py

Type /help for commands. Type /quit to exit.

Internet access:
  * Only the Wikipedia REST API (read-only JSON).
  * No other websites are contacted.
  * You can disable it with /wiki off.
"""

import os
import sys
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# Make sure we can import from basic_llm.py in the same folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from basic_llm import (
    BasicLLM,
    build_vocab,
    encode,
    decode,
    BUILTIN_TEXT,
)
import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MEMORY_FILE = Path("chatbot_memory.json")
HISTORY_LIMIT = 10
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) HybridChatbot/1.0"

MODEL_DIMS = dict(dim=16, num_heads=4, num_layers=1, block_size=32, lr=1e-2)
MODEL_SAVE = "chatbot_model.json"

# ---------------------------------------------------------------------------
# Persistent memory
# ---------------------------------------------------------------------------

def load_memory():
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Memory must be a JSON object")
        data.setdefault("notes", [])
        data.setdefault("wiki_enabled", True)
        data.setdefault("learned_corrections", {})
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return {"notes": [], "wiki_enabled": True, "learned_corrections": {}}


def save_memory(memory):
    MEMORY_FILE.write_text(json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")


memory = load_memory()
conversation = []  # [(user, ai), ...]


# ---------------------------------------------------------------------------
# Wikipedia lookup (read-only)
# ---------------------------------------------------------------------------

def wiki_search(query, limit=3):
    """Return a list of {title, snippet, url} from Wikipedia, or [] on failure."""
    if not memory.get("wiki_enabled", True):
        return []

    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
    })
    url = f"{WIKIPEDIA_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return []

    results = []
    for item in data.get("query", {}).get("search", [])[:limit]:
        title = item.get("title", "")
        snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
        snippet = re.sub(r"\s+", " ", snippet).strip()
        results.append({
            "title": title,
            "snippet": snippet,
            "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
        })
    return results


def wiki_summary(title, sentences=2):
    """Fetch a short extract (summary) for a Wikipedia article title."""
    if not memory.get("wiki_enabled", True):
        return ""

    params = urllib.parse.urlencode({
        "action": "query",
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "exsentences": sentences,
        "titles": title,
        "format": "json",
    })
    url = f"{WIKIPEDIA_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return ""

    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        extract = page.get("extract", "")
        if extract:
            return re.sub(r"\s+", " ", extract).strip()
    return ""


# ---------------------------------------------------------------------------
# Neural language model (from basic_llm.py)
# ---------------------------------------------------------------------------

def build_nn_model(vocab_size):
    return BasicLLM(vocab_size=vocab_size, **MODEL_DIMS)


def nn_generate(model, stoi, itos, prompt, max_tokens=80, temperature=0.9, top_k=8, rep_penalty=1.3):
    """Generate a continuation of the prompt using the neural model."""
    context = np.array([encode(prompt, stoi)], dtype=np.int64)
    out = model.generate(
        context,
        max_new_tokens=max_tokens,
        temperature=temperature,
        top_k=top_k,
        repetition_penalty=rep_penalty,
    )
    return decode(out[0].tolist(), itos)


# ---------------------------------------------------------------------------
# Response logic
# ---------------------------------------------------------------------------

def is_factual_question(text):
    """Heuristic: does the user want a factual definition/explanation?"""
    t = text.casefold().strip()
    starters = (
        "what is", "what are", "who is", "who are", "where is", "where are",
        "when did", "when was", "why is", "why are", "how does", "how do",
        "define", "definition of", "tell me about", "explain",
    )
    return any(t.startswith(s) for s in starters) or "?" in t


def wiki_response(text):
    """Try to answer using Wikipedia. Return (answer, used_wiki)."""
    results = wiki_search(text)
    if not results:
        return None, False

    # Use the top result's summary
    top = results[0]
    summary = wiki_summary(top["title"], sentences=2)
    if summary:
        answer = f"According to Wikipedia: {summary}\n\nSource: {top['url']}"
        return answer, True

    # Fall back to search snippet
    snippet = top.get("snippet", "")
    if snippet:
        answer = f"From Wikipedia search: {snippet}\n\nSource: {top['url']}"
        return answer, True

    return None, False


def nn_response(model, stoi, itos, text):
    """Generate a conversational response using the neural model.

    If the model produces mostly gibberish (too many non-letter characters
    or repeated characters), fall back to a polite canned response.
    """
    prompt = f"User: {text}\nAI:"
    completion = nn_generate(model, stoi, itos, prompt, max_tokens=60)
    # Extract just the AI part
    marker = "AI:"
    idx = completion.find(marker)
    if idx != -1:
        ai_text = completion[idx + len(marker):].strip()
    else:
        ai_text = completion.strip()
    # Cut at the next "User:" if the model hallucinated a dialogue
    cut = ai_text.find("\nUser:")
    if cut != -1:
        ai_text = ai_text[:cut].strip()

    # Heuristic: if the output is mostly punctuation/whitespace or has
    # long repeated runs, it's likely gibberish — use a fallback.
    if not ai_text:
        return "Interesting. Tell me more."
    letters = sum(c.isalpha() for c in ai_text)
    if letters / max(len(ai_text), 1) < 0.5:
        return "I'm not sure how to respond to that yet. Try asking me something factual, or train me with /train."
    # Check for repeated character runs (e.g. "aaaaa")
    if re.search(r"(.)\1{4,}", ai_text):
        return "I'm still learning. Try asking me something factual, or train me with /train."
    return ai_text


# ---------------------------------------------------------------------------
# Training / model management
# ---------------------------------------------------------------------------

def prepare_data():
    stoi, itos = build_vocab(BUILTIN_TEXT)
    data = np.array(encode(BUILTIN_TEXT, stoi), dtype=np.int64)
    return stoi, itos, data


def train_model(model, stoi, itos, data, iters):
    from basic_llm import train
    print(f"[train] starting {iters} iterations on CPU (dim={model.dim}, layers={model.num_layers}, block={model.block_size})...")
    train(model, data, iters=iters, batch_size=8, save_path=MODEL_SAVE)
    print(f"[train] model saved to {MODEL_SAVE}")


# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------

def show_help():
    print("""
Hybrid Chatbot commands:
  /help          Show this help
  /quit          Exit the chatbot
  /wiki on|off   Enable or disable Wikipedia lookups (default: on)
  /train N       Train the neural model for N iterations (e.g. /train 500)
  /memory        Show saved notes
  /remember TEXT Save a note to long-term memory
  /history       Show recent conversation history
  /clear         Clear this session's conversation history
""")


def main():
    print("=" * 50)
    print("Hybrid Neural + Wikipedia Chatbot v0.0.1")
    print("CPU-only — no GPU required")
    print("=" * 50)
    print("Type /help for commands, /quit to exit.\n")

    stoi, itos, data = prepare_data()
    model = build_nn_model(len(stoi))

    if os.path.exists(MODEL_SAVE):
        try:
            model.load(MODEL_SAVE)
            print(f"[model] loaded saved weights from {MODEL_SAVE}")
        except Exception as e:
            print(f"[model] could not load saved weights: {e}")
    else:
        print("[model] no saved weights found — using untrained neural model")
        print("[tip] run /train 300 to improve responses")

    print(f"[wiki] lookups: {'on' if memory.get('wiki_enabled', True) else 'off'}")
    print()

    while True:
        try:
            prompt = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAI> Goodbye.")
            break
        if not prompt:
            continue

        lower = prompt.casefold()

        if lower in {"/quit", "/exit"}:
            print("AI> Goodbye.")
            break
        if lower == "/help":
            show_help()
            continue
        if lower == "/wiki on":
            memory["wiki_enabled"] = True
            save_memory(memory)
            print("AI> Wikipedia lookups enabled.")
            continue
        if lower == "/wiki off":
            memory["wiki_enabled"] = False
            save_memory(memory)
            print("AI> Wikipedia lookups disabled.")
            continue
        if lower.startswith("/train "):
            try:
                iters = int(prompt.split(maxsplit=1)[1])
            except ValueError:
                print("AI> Usage: /train NUMBER")
                continue
            train_model(model, stoi, itos, data, iters)
            continue
        if lower == "/memory":
            if not memory["notes"]:
                print("No saved notes yet.")
            else:
                for i, note in enumerate(memory["notes"], 1):
                    print(f"{i}. [{note.get('source','user')}] {note.get('text','')}")
            continue
        if lower.startswith("/remember "):
            note = prompt[len("/remember "):].strip()
            if note:
                memory["notes"].append({
                    "text": note,
                    "source": "user",
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                })
                save_memory(memory)
                print("AI> Saved that note.")
            else:
                print("AI> Nothing to remember.")
            continue
        if lower == "/history":
            if not conversation:
                print("No conversation history yet.")
            else:
                for i, (u, a) in enumerate(conversation, 1):
                    print(f"{i}. You: {u}\n   AI: {a}")
            continue
        if lower == "/clear":
            conversation.clear()
            print("AI> Conversation history cleared.")
            continue

        # 1. Try Wikipedia for factual questions
        answer = None
        if is_factual_question(prompt):
            wiki_answer, used_wiki = wiki_response(prompt)
            if used_wiki:
                answer = wiki_answer

        # 2. Fall back to neural generation
        if answer is None:
            answer = nn_response(model, stoi, itos, prompt)

        print(f"AI> {answer}")
        conversation.append((prompt, answer))
        conversation[:] = conversation[-HISTORY_LIMIT:]


if __name__ == "__main__":
    main()
