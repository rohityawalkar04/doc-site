"""Runtime topics: compose one when the hand-written queue runs out.

Why a bank and not a model
--------------------------
CI has no LLM - `cloud_run.py` forces `script.mode: beats` because the runner has
no Ollama - and a runtime model would put an unattended channel's editorial floor
behind a network call and a bill. This composes a topic from sentences a person
wrote in `config/topic-bank.yaml`, so the worst case is a mediocre reel, never an
invented statistic or a fabricated quotation.

What it composes
----------------
A theme supplies the subject and the nine steps. Hook, tension, payoff, the call
to action and the title rotate with the UTC date, so a retry on the same day
rebuilds the same topic (idempotent, like the ledger) and tomorrow is a different
one. Only a title nobody has published is acceptable: the ledger, the topic files
and this run's own history are all consulted before a topic is handed to the
build, because the alternative - discovering the collision at upload - costs a
whole render.

Where it is recorded
--------------------
`save_generated()` appends to `config/topics-generated.yaml`, a machine-owned
file. It deliberately does not touch `config/topics.yaml`: a PyYAML round-trip
would strip the comments out of the hand-curated file, and this project's memory
lives in those comments.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from . import config, ledger, llm
from .text import slug, title_key

BANK = "config/topic-bank.yaml"
GENERATED = "config/topics-generated.yaml"

HEADER = """# Composed at runtime by reelforge.topics. Machine-owned: append-only, and
# committed with the ledger so the channel stays auditable. Edit by hand if a
# composed reel was bad, but do not reformat - the writer rewrites this file.
"""


def _bank_path(cfg) -> Path:
    return cfg.path(BANK)


def _generated_path(cfg) -> Path:
    return cfg.path(GENERATED)


def load_bank(cfg) -> dict:
    p = _bank_path(cfg)
    if not p.exists():
        raise SystemExit(
            f"the queue is empty and there is no bank at {p}.\n"
            "  Either add beats to config/topics.yaml, or write a bank to compose from.\n"
            "  See the header of config/topic-bank.yaml for the shape."
        )
    data = yaml.safe_load(p.read_text()) or {}
    if not data.get("themes"):
        raise SystemExit(f"{p} has no themes under `themes:`")
    return data


def known() -> tuple[set[str], set[str]]:
    """(title keys, ids) already spoken for, from the topic files and the ledger.

    Channel-only titles are not here: reading the feed needs the network, and
    `publish()` already fails closed on a title that is live but unrecorded. This
    set is the cheap, always-available half of the novelty check.
    """
    titles: set[str] = set()
    ids: set[str] = set()
    for topic in config.load_topics():
        if topic.get("title"):
            titles.add(title_key(str(topic["title"])))
        if topic.get("id"):
            ids.add(str(topic["id"]))
    for entry in ledger.entries():
        if entry.get("title"):
            titles.add(title_key(str(entry["title"])))
        if entry.get("topic_id"):
            ids.add(str(entry["topic_id"]))
    return titles, ids


def _pick(options: list, r: int, offset: int = 0) -> str:
    if not options:
        raise SystemExit("topic bank has an empty option list")
    return options[(r + offset) % len(options)]


def compose(bank: dict, theme: dict, r: int, *, title_step: int) -> dict:
    """One theme + one rotation -> a full topic dict. No validation here.

    `title_step` is deliberately *not* `r`: indexing the title with the same
    number that picks the theme locks the pair (day six would ask for the theme
    and title of day one), which exhausts the bank in one cycle of themes. Titles
    advance one step per full theme cycle instead, so every (theme, title) pair
    occurs once before anything repeats.

    The nine steps keep the order they were written in: their sequence is the
    argument, and rotating them independently produced a script that opened with
    the fifth action. Variation lives in the framing - title, hook, tension,
    payoff, call to action - and in the per-scene visuals.
    """
    visuals = bank.get("visuals") or {}
    steps = list(theme.get("steps") or [])
    title = _pick(theme.get("titles") or [], title_step)
    narrations = [
        _pick(theme.get("hooks") or [], r, 1),
        _pick(theme.get("tensions") or [], r, 2),
        *steps,
        _pick(theme.get("payoffs") or [], r, 3),
        _pick(theme.get("ctas") or [], r, 4),
    ]
    queries = [
        _pick(visuals.get("hook") or [], r, 1),
        _pick(visuals.get("tension") or [], r, 2),
        *[_pick(visuals.get("step") or [], r, i) for i in range(len(steps))],
        _pick(visuals.get("payoff") or [], r, 3),
        _pick(visuals.get("cta") or [], r, 4),
    ]
    return {
        "id": slug(title),
        "title": title,
        "angle": (theme.get("angle") or "").strip(),
        "hashtags": list(theme.get("hashtags") or bank.get("hashtags") or []),
        "beats": [{"n": n, "v": v} for n, v in zip(narrations, queries)],
        "theme": theme.get("id"),
    }


def generate(cfg, *, when: date | None = None, avoid: set[str] | None = None,
             attempts: int = 40) -> dict:
    """Compose a topic nobody has published. Raises SystemExit when it cannot.

    `attempts` walks the rotation, which changes the theme, the framing and the
    title together - so a used title moves on rather than blocking the night.
    `avoid` is for composing more than one topic in a single run: the ledger is
    not written until a reel publishes, so without it two calls on the same day
    would return the same topic.
    """
    bank = load_bank(cfg)
    themes = bank["themes"]
    titles, ids = known()
    titles |= {title_key(t) for t in (avoid or ())}
    ids |= {slug(t) for t in (avoid or ())}
    today = when or datetime.now(timezone.utc).date()
    base = int(today.strftime("%Y%m%d"))
    findings: list[str] = []

    for step in range(attempts):
        r = base + step
        theme = themes[r % len(themes)]
        topic = compose(bank, theme, r, title_step=r // len(themes))
        if title_key(topic["title"]) in titles or topic["id"] in ids:
            findings.append(f"{topic['title']!r} is already published")
            continue
        script = llm._normalise(llm._from_beats(topic), topic, cfg)
        blocking, advisory = llm._problems(script, cfg)
        if blocking:
            findings.append(f"{topic['title']!r}: {blocking[0]}")
            continue
        topic["_generated"] = True
        topic["_advisory"] = "; ".join(advisory[:2])
        return topic

    raise SystemExit(
        f"could not compose a runtime topic after {attempts} rotations.\n  "
        + "\n  ".join(findings[:6])
        + f"\n  Extend {BANK} (more titles, more themes) - the rotation has run dry."
    )


def save_generated(cfg, topic: dict) -> Path:
    """Append the topic to the machine-owned file. Idempotent on the topic id.

    Called after a successful publish and before the ledger push, so one commit
    carries both the record and the thing it recorded.
    """
    p = _generated_path(cfg)
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    rows = list((data or {}).get("topics") or [])
    clean = {k: v for k, v in topic.items() if not str(k).startswith("_")}
    if any(str(t.get("id")) == str(clean["id"]) for t in rows):
        return p
    rows.append(clean)
    body = yaml.safe_dump({"topics": rows}, sort_keys=False, allow_unicode=True, width=100)
    p.write_text(HEADER + body)
    return p


# --------------------------------------------------------------------------- bank check
def check_bank(cfg) -> list[str]:
    """Structural and editorial defects in the bank. Compose nothing.

    The bank is data, and data drifts: a sentence edited past the word range, a
    theme missing a visual, two titles that are the same. This reports those.

    A title that has *already been used* is deliberately not a defect - it is
    one title fewer for the rotation, which `remaining()` reports. Treating it as
    a defect made the health gate fail permanently after the first composed reel.
    """
    problems: list[str] = []
    bank = load_bank(cfg)
    visuals = bank.get("visuals") or {}
    for role in ("hook", "tension", "step", "payoff", "cta"):
        if not visuals.get(role):
            problems.append(f"visuals.{role} is missing or empty")

    wmin, wmax = cfg.get("script.words_per_scene", [8, 14])
    lo, hi = max(3, int(wmin * 0.55)), int(wmax * 1.6)
    n_scenes = int(cfg.get("script.min_scenes", 13))

    seen_titles: set[str] = set()
    for theme in bank["themes"]:
        tid = theme.get("id") or "<no id>"
        for role in ("titles", "hooks", "tensions", "steps", "payoffs", "ctas"):
            if not theme.get(role):
                problems.append(f"{tid}: `{role}` is missing or empty")
        if not (theme.get("hashtags") or bank.get("hashtags")):
            problems.append(f"{tid}: no hashtags on the theme and none at the bank root")
        if len(theme.get("steps") or []) != n_scenes - 4:
            problems.append(
                f"{tid}: {len(theme.get('steps') or [])} steps; "
                f"script.min_scenes is {n_scenes}, so it needs {n_scenes - 4}"
            )
        for role in ("hooks", "tensions", "payoffs", "ctas"):
            for s in theme.get(role) or []:
                problems.extend(_sentence_problems(f"{tid}.{role}", str(s), lo, hi))
        for s in theme.get("steps") or []:
            problems.extend(_sentence_problems(f"{tid}.steps", str(s), lo, hi))
        for t in theme.get("titles") or []:
            key = title_key(str(t))
            if key in seen_titles:
                problems.append(f"{tid}: duplicate title within the bank: {t!r}")
            seen_titles.add(key)
    return problems


def remaining(cfg) -> tuple[int, int]:
    """(titles the rotation can still use, titles the bank holds).

    This is the number that decides whether a composed reel is possible tonight:
    the ledger and the topic files are consulted, so a title published by an
    earlier run is spent.
    """
    bank = load_bank(cfg)
    used_titles, used_ids = known()
    spent = sum(
        1
        for theme in bank["themes"]
        for title in theme.get("titles") or []
        if title_key(str(title)) in used_titles or slug(str(title)) in used_ids
    )
    total = capacity(bank)
    return max(0, total - spent), total


def capacity(bank: dict) -> int:
    """How many reels the bank can compose before every title is used.

    One title per (theme, title) pair, so this is themes x the smallest title
    list - which is also the number of nights before the rotation must be
    extended. Reported by `--check` so the ceiling is visible rather than
    discovered as a failed run.
    """
    themes = bank.get("themes") or []
    if not themes:
        return 0
    return len(themes) * min(len(t.get("titles") or []) for t in themes)


def _sentence_problems(where: str, text: str, lo: int, hi: int) -> list[str]:
    out: list[str] = []
    words = llm._word_count(text)
    if words < lo or words > hi:
        out.append(f"{where}: {words} words (allowed {lo}-{hi}): {text!r}")
    for b in llm.BANNED:
        if b in text.lower():
            out.append(f"{where}: banned phrase {b!r}: {text!r}")
    if llm.STAT.search(text):
        out.append(f"{where}: looks like an unverifiable number: {text!r}")
    return out


# --------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reelforge topics", description="runtime topic bank")
    ap.add_argument("--check", action="store_true", help="validate the bank, compose nothing")
    ap.add_argument("--next", action="store_true", help="print the topic it would compose now")
    args = ap.parse_args(argv)

    cfg = config.load()
    if args.check or not args.next:
        problems = check_bank(cfg)
        for p in problems:
            print(f"  FAIL {p}")
        bank = load_bank(cfg)
        left, total = remaining(cfg)
        print(f"\ncapacity: {left} of {total} composed reel(s) still available "
              f"({len(bank['themes'])} themes)")
        print(f"{'bank is clean' if not problems else f'{len(problems)} problem(s)'}\n")
        if problems:
            return 1
    if args.next:
        topic = generate(cfg)
        print(yaml.safe_dump({k: v for k, v in topic.items() if not k.startswith("_")},
                             sort_keys=False, allow_unicode=True, width=100))
        print(f"# {len(topic['beats'])} scenes, "
              f"{llm.estimated_seconds(llm._normalise(llm._from_beats(topic), topic, cfg), cfg)}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
