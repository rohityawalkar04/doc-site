"""Script generation: Ollama first, beat-sheet fallback second.

The fallback is not decoration. A pipeline that cannot produce a video when the
local model is down is a pipeline you stop trusting at 7am on a Sunday.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

SYSTEM = """You write short vertical video scripts for a faceless channel.

Rules that are not negotiable:
- Spoken words only. No stage directions, no emoji, no markdown, no surrounding quotes.
- Write for the ear: short sentences, concrete nouns, no corporate filler.
- Never invent statistics or quote numbers you cannot attribute. Speak about the
  viewer's own habits instead.
- No "in this video", "let's dive in", "game-changer", "unlock", "leverage".
"""

TEMPLATE = """CHANNEL: {lens}
AUDIENCE: {audience}
TONE: {tone}
LANGUAGE: {language}

Topic: {title}
Angle to take: {angle}

Write the script. It has EXACTLY {n_scenes} scenes - no more, no fewer.

- Every scene is ONE full spoken sentence of {wmin} to {wmax} words.
  A three-word fragment is not a scene. Never summarise the whole idea in six lines.
- Scene 1 is the hook: a specific claim, a contradiction, or something about the
  viewer's own behaviour. Never an introduction.
- Scene 2 is the tension: why the obvious approach fails.
- Scenes 3 to {last_step} each give ONE concrete step the viewer can actually do.
- Scene {payoff} is the payoff: what changes once they do it.
- Scene {n_scenes} is a one-line call to action.
- visual_query: 2 to 5 plain English words describing generic b-roll for that scene.
- title: under 80 characters, sentence case, no clickbait, no ALL CAPS.
- description: 1 to 2 plain sentences.
- caption: one sentence for Instagram.
- hashtags: 6 to 9 tags, each starting with #, no spaces, broad plus specific.
- cta: one short sentence.

The "scenes" array MUST contain exactly {n_scenes} objects.

Return ONLY this JSON shape:
{{"title": "...", "description": "...", "caption": "...", "hashtags": ["#a", "#b"],
  "cta": "...", "scenes": [{{"narration": "...", "visual_query": "..."}}]}}
"""


def _ollama_chat(cfg, prompt: str) -> dict:
    """Try configured local models in order; continue when a model is missing or fails."""
    host = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    models = cfg.get("llm.models") or [cfg.get("llm.model", "qwen2.5:7b-instruct")]
    if isinstance(models, str):
        models = [models]
    last_error: Exception | None = None
    for model in models:
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": float(cfg.get("llm.temperature", 0.8)),
                    "num_predict": 1200,
                    "num_ctx": 4096,
                },
            }
        ).encode()
        req = urllib.request.Request(
            host + "/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=int(cfg.get("llm.timeout", 240))) as r:
                payload = json.load(r)
            result = json.loads(payload["message"]["content"])
            result["_llm_model"] = model
            return result
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
            last_error = e
            print(f"    ! Ollama model {model} unavailable/failed: {type(e).__name__}: {e}")
    if last_error:
        raise last_error
    raise RuntimeError("No local LLM models configured")


BANNED = [
    "in this video", "in today's video", "let's dive", "game-changer", "game changer",
    "unlock", "leverage", "dive in", "buckle up", "without further ado",
]

# Heuristic for unverifiable claims. A small model will invent statistics unless
# stopped; a human-written beat sheet beats a confident fabrication.
# Note the `\b` placement: `%` and a following space are both non-word, so a
# trailing \b after "%" never matches and "90% of..." slipped through.
STAT = re.compile(
    r"\bfor every\b|\b(?:studies|research|experts)\s+(?:show|say|found)\b"
    r"|\b\d+(?:\.\d+)?\s*%|\b\d+\s*percent\b"
    r"|\b\d+\s*(?:hours?|minutes?|days?|weeks?|months?|years?)\b",
    re.I,
)


def estimated_seconds(script: dict, cfg) -> float:
    """Word count -> seconds, calibrated on this voice (~3 words/s + a tail per scene).

    Cheaper than synthesising 10 scenes just to discover the script is 18 s long.
    """
    wps = float(cfg.get("script.words_per_second", 3.0))
    tail = float(cfg.get("audio.tail_pad", 0.30))
    scenes = script.get("scenes") or []
    words = sum(_word_count(s["narration"]) for s in scenes)
    return round(words / wps + tail * len(scenes), 1)


SCRIPTURE_ATTR = re.compile(
    r"\b(?:gita|krishna|chanakya|vedas?|upanishad?s?|ramayana|mahabharata|manusmriti)\b"
    r"[^.;]{0,50}\b(?:says?|said|teach(?:es)?|tells? us|commands?|promises?|warns?)\b",
    re.I,
)


def _problems(script: dict, cfg) -> tuple[list[str], list[str]]:
    """Returns (blocking, advisory).

    Blocking = a claim we cannot stand behind, or filler that reads as slop.
    Advisory = length/shape. A good 8-scene script should not be discarded just
    because 9 were requested.
    """
    blocking: list[str] = []
    advisory: list[str] = []
    wmin, wmax = cfg.get("script.words_per_scene", [8, 16])
    lo, hi = max(3, int(wmin * 0.55)), int(wmax * 1.6)

    n = len(script["scenes"])
    nlo, nhi = int(cfg.get("script.min_scenes", 6)), int(cfg.get("script.max_scenes", 8))
    if n < nlo - 2:
        advisory.append(f"{n} scenes; want at least {nlo}")
    elif n > nhi + 2:
        advisory.append(f"{n} scenes; want at most {nhi}")

    for i, s in enumerate(script["scenes"], 1):
        words = _word_count(s["narration"])
        if words < lo or words > hi:
            advisory.append(f"scene {i} is {words} words; keep every scene between {lo} and {hi}")
        if STAT.search(s["narration"]):
            blocking.append(f"scene {i} states an unverifiable number: {s['narration']!r}")
        low = s["narration"].lower()
        for b in BANNED:
            if b in low:
                blocking.append(f"scene {i} uses the banned phrase {b!r}")
        # In this niche the worst failure is a fluent invented quotation. A claim
        # attributed to a text is only allowed when the topic names its source.
        if SCRIPTURE_ATTR.search(s["narration"]) and not script.get("source"):
            blocking.append(
                f"scene {i} attributes a claim to a scripture with no source: "
                f"{s['narration']!r}"
            )
    if len(script["scenes"]) < 4:
        blocking.append(f"only {len(script['scenes'])} scenes; a reel needs at least 4")

    # Duration is estimated in words, before a single second of audio is made.
    # It is advisory, never blocking: a safety-clean script that runs a few
    # seconds short is still shippable, and discarding it wastes a good script.
    target = float(cfg.get("script.target_seconds", 35))
    est = estimated_seconds(script, cfg)
    if est < target * 0.8:
        advisory.append(
            f"the script would run about {est:.0f} seconds; it needs about "
            f"{target:.0f}. Add more substance to each scene - do not pad with filler."
        )
    elif est > target * 1.35:
        advisory.append(f"the script would run about {est:.0f} seconds; trim to about {target:.0f}")
    return blocking, advisory


def _clean_text(s: str) -> str:
    s = str(s or "").strip()
    s = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", s)
    s = re.sub(r"\s+", " ", s)
    # Strip list markers only ("- ", "1. ", "2) "). A bare leading digit is
    # content: "3-line reminder" must survive.
    s = re.sub(r"^\s*(?:[-*•·]|\d+[.)])\s+", "", s)
    s = re.sub(r"[*_`]", "", s)
    return s.strip()


def _word_count(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'’-]+", s))


def _normalise(raw: dict, topic: dict, cfg) -> dict:
    scenes_in = raw.get("scenes") or []
    scenes: list[dict] = []
    for s in scenes_in:
        if isinstance(s, str):
            s = {"narration": s, "visual_query": ""}
        narration = _clean_text(s.get("narration", ""))
        if not narration:
            continue
        scenes.append(
            {
                "narration": narration,
                "visual_query": _clean_text(s.get("visual_query", "")) or topic["title"],
            }
        )

    wmax = int(cfg.get("script.words_per_scene", [8, 16])[1])
    # A scene that runs long is split, not truncated: truncation silently loses the point.
    split: list[dict] = []
    for s in scenes:
        words = s["narration"].split()
        if len(words) <= wmax * 1.6:
            split.append(s)
            continue
        mid = len(words) // 2
        split.append({**s, "narration": " ".join(words[:mid])})
        split.append({**s, "narration": " ".join(words[mid:])})

    hashtags = [h if h.startswith("#") else f"#{h}" for h in (raw.get("hashtags") or [])]
    hashtags = [re.sub(r"[^#\w]", "", h) for h in hashtags if h.strip("#")]
    if len(hashtags) < 5:
        # Per-topic tags win: a Ramayana reel and a Greek one do not share search.
        hashtags = [h if h.startswith("#") else f"#{h}" for h in (topic.get("hashtags") or [])]
        hashtags = [re.sub(r"[^#\w]", "", h) for h in hashtags if h.strip("#")]
    if len(hashtags) < 5:
        hashtags += ["#shorts", "#motivation", "#hindumythology", "#wisdom"]

    return {
        "title": _clean_text(raw.get("title")) or topic["title"],
        "description": _clean_text(raw.get("description")) or topic.get("angle", "").strip(),
        "caption": _clean_text(raw.get("caption")) or topic["title"],
        "hashtags": hashtags[:9],
        "cta": _clean_text(raw.get("cta")),
        "source": (topic.get("source") or "").strip(),
        "scenes": split,
    }


def _from_beats(topic: dict) -> dict:
    """Beats may be plain strings or {n: narration, v: visual query}.

    The dict form matters for AI visuals: without an explicit query every scene
    would be generated from the topic title and the reel would look identical
    shot to shot.
    """
    raw = topic.get("beats") or []
    scenes: list[dict] = []
    for b in raw:
        if isinstance(b, dict):
            narration = _clean_text(b.get("n", ""))
            visual = _clean_text(b.get("v", "")) or topic["title"]
        else:
            narration = _clean_text(b)
            visual = topic["title"]
        if narration:
            scenes.append({"narration": narration, "visual_query": visual})

    if not scenes:
        fallback = [topic["title"], (topic.get("angle") or "").strip()]
        scenes = [
            {"narration": t, "visual_query": topic["title"]} for t in fallback if t
        ]

    return {
        "title": topic["title"],
        "description": (topic.get("angle") or "").strip().replace("\n", " "),
        "caption": topic["title"],
        # Per-topic tags must survive: a Ramayana reel and a Greek one do not
        # share a search audience, so a global default is actively wrong.
        "hashtags": topic.get("hashtags") or [
            "#shorts", "#motivation", "#hindumythology", "#wisdom", "#mindset",
        ],
        "cta": "",
        "scenes": scenes,
    }


def generate(cfg, topic: dict) -> tuple[dict, str]:
    """Returns (script, source) where source is 'ollama' or 'fallback'.

    Attempts are scored, not accepted/rejected: a safety-clean script that runs a
    few seconds short is still worth shipping, so it is kept as the best candidate
    and only *unsafe* scripts cause a fallback.
    """
    # Ask for one exact count, not a range: a range is satisfiable at its minimum,
    # and small models always take the minimum.
    n_scenes = int(cfg.get("script.min_scenes", 10))
    prompt = TEMPLATE.format(
        lens=cfg.get("lens", ""),
        audience=cfg.get("audience", ""),
        tone=cfg.get("tone", ""),
        language=cfg.get("language", "en"),
        title=topic["title"],
        angle=" ".join((topic.get("angle") or "").split()),
        n_scenes=n_scenes,
        last_step=n_scenes - 2,
        payoff=n_scenes - 1,
        wmin=cfg.get("script.words_per_scene", [8, 14])[0],
        wmax=cfg.get("script.words_per_scene", [8, 14])[1],
        target_seconds=cfg.get("script.target_seconds", 35),
    )
    target = float(cfg.get("script.target_seconds", 32))

    # Beats mode skips the model entirely. Hand-written beats are the quality
    # floor and the only way to get an exact scene count and visual queries.
    if str(cfg.get("script.mode", "auto")).lower() == "beats":
        script = _normalise(_from_beats(topic), topic, cfg)
        return script, "beats"

    def score(script: dict) -> tuple[int, float]:
        safety, _ = _problems(script, cfg)
        return len(safety), abs(estimated_seconds(script, cfg) - target)

    best: dict | None = None
    best_score: tuple[int, float] | None = None
    reason = "no attempt produced a usable script"
    attempt_prompt = prompt

    for attempt in range(1, int(cfg.get("script.attempts", 3)) + 1):
        try:
            raw = _ollama_chat(cfg, attempt_prompt)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
            reason = f"{type(e).__name__}: {e}"
            break
        script = _normalise(raw, topic, cfg)
        script["_llm_model"] = raw.get("_llm_model")
        safety, advisory = _problems(script, cfg)
        s = score(script)
        if best_score is None or s < best_score:
            best, best_score = script, s
        if not safety and s[1] <= target * 0.35 and estimated_seconds(script, cfg) >= float(
            cfg.get("script.min_seconds", 0)
        ):
            if advisory:
                best["_advisory"] = "; ".join(advisory[:3])
            return best, "ollama"
        reason = ("script still unsafe: " + "; ".join(safety[:3])) if safety else (
            f"about {estimated_seconds(script, cfg):.0f}s vs {target:.0f}s target"
        )
        attempt_prompt = prompt + (
            "\n\nYour previous attempt failed:\n- "
            + "\n- ".join((safety + advisory)[:8] or [reason])
            + "\n\nKeep everything that worked. Fix only the problems above and"
            " return the full corrected JSON."
        )

    if best is not None and not _problems(best, cfg)[0]:
        best["_advisory"] = reason
        return best, "ollama"

    script = _normalise(_from_beats(topic), topic, cfg)
    script["_fallback_reason"] = reason
    return script, "fallback"
