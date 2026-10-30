#!/usr/bin/env python3
"""
Learning AI prototype:
- Keeps recent conversation history so it can respond to follow-ups.
- Saves durable notes to a local JSON file.
- Splits research prompts into focused web searches and combines snippets.
- Uses only Python's standard library.

Note: this is a small rule-based prototype, not a neural language model.
"""

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

MEMORY_FILE = Path("learning_ai_memory.json")
HISTORY_LIMIT = 12
MAX_SEARCHES = 5
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) LearningAI/1.0"

def load_memory():
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Memory must be a JSON object")
        data.setdefault("notes", [])
        data.setdefault("learned_words", {})
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return {"notes": [], "learned_words": {}}

memory = load_memory()
conversation = []  # Short-term memory; kept in order for this running session.
last_ai_response = ""

def save_memory():
    MEMORY_FILE.write_text(
        json.dumps(memory, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

def remember_note(text, source="user", comment="", prompt=""):
    note = {
        "text": text.strip(),
        "source": source,
        "saved_at": datetime.now().isoformat(timespec="seconds")
    }
    if prompt.strip():
        note["prompt"] = prompt.strip()
    if comment.strip():
        note["comment"] = comment.strip()
    if note["text"] and not any(
        n.get("text", "").casefold() == note["text"].casefold()
        for n in memory["notes"]
    ):
        memory["notes"].append(note)
        save_memory()
        return True
    return False

def clean_text(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()

def search_duckduckgo(query, limit=5):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=12) as response:
        page = response.read().decode("utf-8", errors="replace")

    results = []
    # Parse each result anchor and nearby result container.
    blocks = re.findall(
        r'(?is)<div[^>]+class=["\'][^"\']*result[^"\']*["\'][^>]*>.*?</div>\s*</div>',
        page
    )
    if not blocks:
        blocks = re.findall(
            r'(?is)<a[^>]+class=["\'][^"\']*result__a[^"\']*["\'][^>]*>.*?</a>',
            page
        )

    for block in blocks:
        anchor = re.search(
            r'(?is)<a[^>]+class=["\'][^"\']*result__a[^"\']*["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            block
        )
        if not anchor:
            # Some pages put href after class or vary attribute order.
            anchor = re.search(
                r'(?is)<a\b(?=[^>]*class=["\'][^"\']*result__a[^"\']*["\'])(?=[^>]*href=["\']([^"\']+)["\'])[^>]*>(.*?)</a>',
                block
            )
        if not anchor:
            continue
        link = html.unescape(anchor.group(1))
        title = clean_text(anchor.group(2))
        snippet_match = re.search(
            r'(?is)class=["\'][^"\']*(?:result__snippet|result-snippet)[^"\']*["\'][^>]*>(.*?)</',
            block
        )
        snippet = clean_text(snippet_match.group(1)) if snippet_match else ""
        if link.startswith("//"):
            link = "https:" + link
        if "uddg=" in link:
            parsed = urllib.parse.urlparse(link)
            link = urllib.parse.parse_qs(parsed.query).get("uddg", [link])[0]
        if title and link.startswith("http"):
            results.append({"title": title, "snippet": snippet, "url": link})
        if len(results) >= limit:
            break
    return results

def search_wikipedia(query, limit=5):
    params = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "format": "json", "utf8": 1
    })
    url = "https://en.wikipedia.org/w/api.php?" + params
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=12) as response:
        data = json.loads(response.read().decode("utf-8"))
    results = []
    for item in data.get("query", {}).get("search", [])[:limit]:
        title = item.get("title", "")
        results.append({
            "title": title,
            "snippet": clean_text(item.get("snippet", "")),
            "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
        })
    return results

def web_search(query, limit=5):
    try:
        results = search_duckduckgo(query, limit)
        if results:
            return results
    except Exception:
        pass
    return search_wikipedia(query, limit)

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "for", "and", "or", "but", "with", "about",
    "what", "why", "how", "when", "where", "who", "which", "it", "this",
    "that", "you", "your", "i", "me", "my", "we", "they", "them", "do",
    "does", "did", "can", "could", "would", "should", "please", "tell",
    "explain", "find", "search", "research", "learn", "give", "some"
}

def keywords(text):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]

def split_prompt(prompt):
    """Create focused queries from a prompt, avoiding tiny generic fragments."""
    prompt = prompt.strip()
    quoted = re.findall(r'"([^"]{3,})"|\'([^\']{3,})\'', prompt)
    queries = [a or b for a, b in quoted]
    words = keywords(prompt)
    if len(words) <= 7:
        queries.append(prompt)
    else:
        # Keep the original topic and split remaining keywords into meaningful groups.
        queries.append(" ".join(words[:7]))
        for start in range(7, min(len(words), 28), 5):
            group = words[start:start + 5]
            if len(group) >= 3:
                queries.append(" ".join(group))
    unique = []
    seen = set()
    for q in queries:
        q = q.strip()
        key = q.casefold()
        if q and key not in seen:
            unique.append(q)
            seen.add(key)
        if len(unique) >= MAX_SEARCHES:
            break
    return unique or [prompt]

def similarity_score(query, result):
    qwords = set(keywords(query))
    text = (result.get("title", "") + " " + result.get("snippet", "")).lower()
    return sum(1 for word in qwords if word in text)

def research(prompt):
    queries = split_prompt(prompt)
    print("\nI split your prompt into these searches:")
    for i, q in enumerate(queries, 1):
        print(f"  {i}. {q}")

    merged = []
    seen_urls = set()
    for query in queries:
        print(f"\nSearching: {query}")
        try:
            results = web_search(query, limit=5)
        except Exception as exc:
            print(f"  Search failed: {exc}")
            continue
        for result in results:
            url = result.get("url", "")
            title = result.get("title", "").casefold()
            # Ignore obvious search ads, click trackers, and ad landing redirects.
            if not url or url in seen_urls:
                continue
            parsed_url = urllib.parse.urlparse(url)
            host = parsed_url.netloc.casefold()
            if (
                "y.js" in parsed_url.path
                or "aclick" in parsed_url.path
                or "ad_domain" in parsed_url.query
                or "ad_provider" in parsed_url.query
                or "click_metadata" in parsed_url.query
                or "sponsored" in title
            ):
                continue
            result["score"] = similarity_score(query, result)
            result["query"] = query
            merged.append(result)
            seen_urls.add(url)

    if not merged:
        print("\nI couldn't retrieve results. Check your internet connection and try again.")
        return []

    # Relevance filter: for short prompts, discard results whose titles/snippets
    # do not mention any meaningful search term. This reduces unrelated matches.
    meaningful_terms = set(keywords(prompt))
    if meaningful_terms:
        relevant = []
        for item in merged:
            combined_text = (item.get("title", "") + " " + item.get("snippet", "")).casefold()
            if any(term in combined_text for term in meaningful_terms):
                relevant.append(item)
        if relevant:
            merged = relevant
    merged.sort(key=lambda r: r.get("score", 0), reverse=True)
    merged = merged[:8]
    print("\nCombined research notes:")
    for i, item in enumerate(merged, 1):
        print(f"\n[{i}] {item.get('title', '(untitled)')}")
        if item.get("snippet"):
            print("    " + item["snippet"])
        print("    URL: " + item.get("url", ""))

    print("\nSynthesis:")
    print("- These notes were collected from multiple searches based on your prompt.")
    print("- Snippets may omit context or disagree; open sources before relying on them.")
    print("- This prototype does not read every full webpage or independently verify facts.")
    if input("\nSave these snippets as unverified training notes? [y/N] ").strip().lower() == "y":
        comment = input(
            "Optional comment for this prompt/research (press Enter to skip): "
        ).strip()
        count = 0
        for item in merged:
            note_text = f"{item.get('title', '')}: {item.get('snippet', '')} ({item.get('url', '')})"
            if remember_note(note_text, source="web", comment=comment, prompt=prompt):
                count += 1
        learn_patterns(prompt)
        print(f"Saved {count} new candidate notes.")
    return merged

def learn_patterns(text):
    words = re.findall(r"\b[\w'-]+\b", text.lower())
    for a, b in zip(words, words[1:]):
        memory["learned_words"].setdefault(a, [])
        if b not in memory["learned_words"][a]:
            memory["learned_words"][a].append(b)
    save_memory()

def greeting_response(text):
    normalized = re.sub(r"[^a-z ]", "", text.lower()).strip()
    greetings = {
        "hi", "hello", "hey", "good morning", "goodmorning",
        "good afternoon", "goodafternoon", "good evening", "goodevening",
        "howdy"
    }
    if normalized in greetings:
        if normalized in {"good morning", "goodmorning"}:
            return "Good morning! What would you like to learn about?"
        if normalized in {"good afternoon", "goodafternoon"}:
            return "Good afternoon! What would you like to learn about?"
        if normalized in {"good evening", "goodevening"}:
            return "Good evening! What would you like to learn about?"
        return "Hello! What would you like to learn about?"
    return None

def learned_response(text):
    """Use saved user corrections to answer matching future prompts."""
    prompt = text.casefold().strip()

    for note in reversed(memory.get("notes", [])):
        comment = note.get("comment", "").strip()
        original_prompt = note.get("prompt", "").casefold().strip()

        if not comment or not original_prompt:
            continue

        # Apply a correction when the user repeats the original term.
        if prompt == original_prompt:
            return comment

        # Apply the correction to a word the user explicitly taught us.
        if prompt in original_prompt.split():
            if prompt in comment.casefold():
                return comment

    return None

def conversational_response(text):
    """Handle short social replies before considering web research."""
    normalized = re.sub(r"[^a-z0-9'\s]", "", text.casefold()).strip()
    compact = normalized.replace(" ", "")

    # Short acknowledgements and reactions are not search queries.
    if normalized in {"cool", "thats cool", "that's cool", "pretty cool", "very cool", "nice", "neat", "awesome", "great", "sweet"}:
        return "Glad you think so. What would you like to do next?"
    if normalized in {"ok", "okay", "k", "kk", "alright", "all right", "got it", "understood"}:
        return "Okay."
    if normalized in {"thanks", "thank you", "thx", "ty", "thanks a lot"}:
        return "You're welcome."
    if normalized in {"wow", "whoa", "woah"}:
        return "Yeah, that's a lot to take in."
    if normalized in {"lol", "lmao", "haha", "hehe"}:
        return "🙂"
    if normalized in {"hmm", "hmmm", "interesting"}:
        return "What part are you thinking about?"
    if normalized in {"yes", "yeah", "yep", "yup", "sure"}:
        return "Okay—go ahead."
    if normalized in {"no", "nope", "nah"}:
        return "Okay. What would you prefer instead?"
    if normalized in {"i dont understand", "i don't understand", "dont understand", "don't understand", "im confused", "i'm confused", "that makes no sense"}:
        if conversation and conversation[-1].get("ai"):
            return "I'll explain it more simply. My previous response was: " + conversation[-1]["ai"]
        return "Which part is confusing? I'll try to explain it more simply."
    if normalized in {"you just said that", "u just said that", "you already said that", "you repeated yourself"}:
        return "You're right—I repeated myself. I'll use the conversation context instead of searching that sentence."
    return None


def topic_from_text(text):
    """Extract a lightweight topic hint from the current conversation."""
    words = keywords(text)
    if not words:
        return ""
    # Prefer the user's most recent substantive topic over filler words.
    return " ".join(words[:6])

def is_acknowledgment(text):
    normalized = re.sub(r"[^a-z0-9'\s]", "", text.casefold()).strip()
    variants = {
        "mmm", "mm", "hmm", "hmmm", "mmm okay", "mm okay", "hmm okay",
        "mmm ok", "mm ok", "okay", "ok", "k", "kk", "alright", "all right",
        "gotcha", "i see", "makes sense", "fair enough", "right"
    }
    return normalized in variants

def topic_aware_response(text):
    """Handle acknowledgements and clarifications using recent user context."""
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    prior_topic = ""
    # Look backward for the last substantive user prompt, skipping short reactions.
    for turn in reversed(conversation):
        candidate = turn.get("user", "").strip()
        if candidate and not is_acknowledgment(candidate) and not greeting_response(candidate):
            if not candidate.startswith("/"):
                prior_topic = candidate
                break

    if is_acknowledgment(text):
        if prior_topic:
            return f"Okay. We're still talking about {prior_topic}. What would you like to know about it?"
        return "Okay."

    # Clarifications such as "just the facial hair thing, not barbers"
    # should refine the previous topic rather than trigger a standalone search.
    if prior_topic and any(phrase in normalized for phrase in (
        "not barbers", "not barber", "the facial hair thing", "i mean the facial hair",
        "i meant the facial hair", "not the barber", "not haircuts",
        "i mean", "i meant", "not that", "rather than"
    )):
        if "mustach" in prior_topic or "moustach" in prior_topic or "facial hair" in normalized:
            return "Got it—you mean mustaches as facial hair, not barbers. A mustache is the hair that grows on the upper lip."
        return f"Got it. I'll treat that as a clarification of our previous topic: {prior_topic}."

    return None


def followup_response(text):
    """Use recent turns to recognize references to the conversation."""
    global last_ai_response
    t = text.casefold().strip()
    normalized = re.sub(r"[^\w\s']", "", t)
    previous_user = conversation[-1]["user"] if conversation else ""
    previous_ai = conversation[-1]["ai"] if conversation else ""

    if any(phrase in normalized for phrase in (
        "u just said that", "you just said that", "you already said that",
        "you said that already", "you repeated yourself", "you just repeated"
    )):
        if previous_ai:
            return "You're right—I repeated myself. I'll use the conversation context instead of searching that sentence."
        return "You're right. I'll keep track of what I just said."

    if normalized in {"you just said that", "u just said that", "you repeated yourself"}:
        return "You're right—I repeated myself. I'll try not to repeat that response."

    if normalized in {"why", "why", "how so", "what do you mean", "explain that"} and previous_ai:
        return "I was referring to this: " + previous_ai

    if normalized in {"repeat that", "what did you say", "what did you just say"} and previous_ai:
        return "My previous response was: " + previous_ai

    # Detect an exact consecutive repeat of the user's previous prompt.
    if previous_user and t == previous_user.casefold().strip():
        return "You just sent the same message again. Do you want me to repeat my answer or do something different?"

    # Very simple follow-up handling, anchored in recent context.
    if previous_user and any(p in normalized for p in ("that", "it", "those", "they", "them")):
        if len(normalized.split()) <= 8 and previous_ai:
            return "Are you referring to my last answer? I said: " + previous_ai

    return None

def show_memory():
    if not memory["notes"]:
        print("No saved notes yet.")
        return
    for i, note in enumerate(memory["notes"], 1):
        print(f"{i}. [{note.get('source', 'unknown')}] {note.get('text', '')}")

def main():
    global last_ai_response
    print("Learning AI — conversation memory + web research")
    print("Ordinary prompts are researched online by default.")
    print("Short-term conversation history lasts for this session; saved notes persist in learning_ai_memory.json.")
    print("Type /help for commands.\n")

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
            print("Commands: /remember TEXT, /memory, /forget NUMBER, /web PROMPT, /auto-web on|off, /history, /clear-history, /quit")
            continue
        if lower == "/memory":
            show_memory()
            continue
        if lower == "/history":
            if not conversation:
                print("No conversation history in this session.")
            else:
                for i, turn in enumerate(conversation, 1):
                    print(f"{i}. You: {turn['user']}\n   AI: {turn['ai']}")
            continue
        if lower == "/clear-history":
            conversation.clear()
            last_ai_response = ""
            print("Cleared this session's conversation history. Saved notes were kept.")
            continue
        if lower.startswith("/remember "):
            note = prompt[len("/remember "):].strip()
            if remember_note(note):
                print("AI> Saved that note to long-term memory.")
            else:
                print("AI> That note is empty or already saved.")
            continue
        if lower.startswith("/forget "):
            try:
                index = int(prompt.split(maxsplit=1)[1]) - 1
                if 0 <= index < len(memory["notes"]):
                    removed = memory["notes"].pop(index)
                    save_memory()
                    print("AI> Forgot: " + removed.get("text", ""))
                else:
                    print("AI> That note number does not exist.")
            except (ValueError, IndexError):
                print("Usage: /forget NUMBER")
            continue
        if lower.startswith("/auto-web "):
            setting = lower.split(maxsplit=1)[1].strip()
            if setting in {"on", "off"}:
                memory["auto_web"] = setting == "on"
                save_memory()
                print(f"AI> Automatic web research is {setting}.")
            else:
                print("Usage: /auto-web on|off")
            continue
        if lower.startswith("/web "):
            answer = "I gathered these research notes for your prompt."
            research(prompt[5:].strip())
            conversation.append({"user": prompt, "ai": answer})
            conversation[:] = conversation[-HISTORY_LIMIT:]
            last_ai_response = answer
            continue

        # Handle social/contextual turns before web research.
        answer = greeting_response(prompt)

        if answer is None:
            answer = learned_response(prompt)

        if answer is None:
            answer = topic_aware_response(prompt)
        if answer is None:
                answer = conversational_response(prompt)
        if answer is None:
            answer = followup_response(prompt)

        if answer is not None:
            print("AI> " + answer)
            conversation.append({"user": prompt, "ai": answer})
            conversation[:] = conversation[-HISTORY_LIMIT:]
            last_ai_response = answer
            continue

        auto_web = memory.get("auto_web", True)
        if auto_web:
            research(prompt)
            answer = "I collected research notes for that topic. Ask a focused follow-up, or use /memory to inspect saved notes."
        else:
            answer = "I don't have a full language model, but I can remember notes. Use /remember TEXT to save a fact or /web PROMPT to research online."
            print("AI> " + answer)

        conversation.append({"user": prompt, "ai": answer})
        conversation[:] = conversation[-HISTORY_LIMIT:]
        last_ai_response = answer

if __name__ == "__main__":
    main()
