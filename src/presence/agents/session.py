"""The message session — Presence interviews the person for a few minutes,
proposes angles, and writes from what they said (docs/design/message-session.md).

Pure logic. The model is reached only through ``ask_json``; the checks are
deterministic; every sentence of a draft carries the id of what it came from,
and a sentence without one is removed, not softened. Nothing here sends
anything anywhere."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from presence.core.budget import BudgetExhausted
from presence.core.structured import StructuredError, ask_json

Ask = Callable[[str, str, dict[str, Any]], dict[str, Any]]  # (system, prompt, schema) -> answer

BUDGET_MESSAGE = "today's model budget is used up — try again tomorrow, or raise it in settings"

CHANNELS: dict[str, dict[str, Any]] = {
    "email": {"label": "email", "words": (90, 140)},
    "linkedin_message": {"label": "LinkedIn message", "chars": 600},
    "connection_note": {"label": "connection note", "chars": 280},
}
TONES: dict[str, str] = {
    "plain": "plain — short and direct, no warm-up",
    "warmer": "a little warmer — still short; one friendly line is fine",
}
BANNED = ("I am writing to", "I believe I would be a great fit", "passionate", "leverage",
          "synergy", "excited to")

# The pool the model picks from and rephrases for the job; never the same one twice.
QUESTIONS = (
    "Why this one, honestly?",
    "Have you done anything that touches their problem — even small, even outside work?",
    "What did you notice about them: the product, a post, the way the ad is written?",
    "What would you do in your first month, or what would you ask them?",
    "Is there a story you tell about yourself that fits here?",
    "How do you want to sound?",
)

MAX_TEXT = 6000  # per pasted block in a prompt; keeps a small local model inside its context
KEPT_VERDICTS = ("accepted", "edited")


@dataclass
class Context:
    """Everything the model may see for one job. Stories are the confirmed ones only."""

    company: str
    title: str
    status: str = ""
    timeline: str = ""
    description: str = ""
    notes: str = ""
    cv_label: str = ""
    cv_facts: list[dict[str, str]] = field(default_factory=list)
    stories: list[dict[str, Any]] = field(default_factory=list)
    chosen: list[str] = field(default_factory=list)  # story ids the person ticked "use"
    transcript: list[dict[str, Any]] = field(default_factory=list)
    angles: list[dict[str, Any]] = field(default_factory=list)
    tone: str = "plain"
    profile: dict[str, Any] = field(default_factory=dict)  # confirmed facts only: name, links
    wants_more: bool = False  # the person pressed "ask me more" past the cap

    def answers(self) -> list[dict[str, Any]]:
        return [t for t in self.transcript if t.get("answer") and not t.get("skipped")]

    def kept_angles(self) -> list[dict[str, Any]]:
        return [a for a in self.angles if a.get("verdict") in KEPT_VERDICTS]


TURN = {
    "type": "object",
    "properties": {
        "question": {"type": "string",
                     "description": "the next question, in plain words, not one already asked"},
        "why": {"type": "string",
                "description": "at most twelve words, to the person, e.g. 'because their ad is "
                               "about no-shows'"},
        "angle": {"type": "string",
                  "description": "one plain sentence TO the person about how something they "
                                 "said or did meets a line in the job — e.g. 'Your reporting "
                                 "rebuild meets their line about owning the monthly numbers.' "
                                 "Empty when nothing supports one yet. Never instructions."},
        "angle_from": {"type": "string",
                       "description": "the id that supports the angle: answer:N or story:ID"},
        "enough": {"type": "boolean",
                   "description": "true when there is material for a specific message"},
    },
    "required": ["question", "why", "angle", "angle_from", "enough"],
}
DRAFT = {  # the message itself, as prose — small models write far better this way
    "type": "object",
    "properties": {"message": {"type": "string",
                               "description": "the whole message as plain text: greeting on its "
                                              "own line, the body, then the sign-off and name on "
                                              "their own lines"}},
    "required": ["message"],
}
ATTRIBUTION = {  # then: where did each sentence come from?
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object",
        "properties": {"n": {"type": "integer"},
                       "source": {"type": "string",
                                  "description": "the one id the sentence comes from, or an "
                                                 "empty string when nothing in the material "
                                                 "supports it"}},
        "required": ["n", "source"]}}},
    "required": ["sentences"],
}
EXAMPLE_MESSAGE = (  # a worked example does more for a small model than any rule
    "Hello,\n"
    "I applied for the Junior Analyst role at Harbour Foods on Tuesday. Your ad mentions the "
    "weekly stock report that the buyers never open; on my placement at Lark Books I rebuilt a "
    "report like that, and the buyers started reading it because it fit on one page. Is there "
    "anything else you need from me while the shortlist is decided?\n"
    "Best,\n"
    "Sam"
)
ATTRIBUTION_SYSTEM = (
    "You are given a message, sentence by sentence, and the material it was written from, each "
    "piece with an id. For each sentence say which one id it comes from. A greeting, a sign-off "
    "or the person's name comes from profile. A sentence that refers to the job or the company "
    "comes from description. A sentence that nothing in the material supports gets an empty "
    "string. Answer with JSON only."
)

TURN_SYSTEM = (
    "You help a person prepare a short message to a company about a job. Interview them, one "
    "question at a time, in plain words, to find what would impress the reader beyond the CV: "
    "something specific they noticed, tried, or want to ask. Draw on the job description and "
    "the notes about the company. Never invent anything about the person and never repeat a "
    "question already asked. Propose an angle only when one of their answers or a confirmed "
    "story supports it, and say which. An angle is one plain sentence written to the person, "
    "never a note to yourself. Set enough to true when there is material for a specific "
    "message, usually after three to six answers. Answer with JSON only."
)
COMPOSE_SYSTEM = (
    "You write a short message that a person will send themselves to a company about a job. "
    "You speak as the person, in the first person. Use only the material given, each piece "
    "with its id: the person's answers, the angles they kept, their confirmed stories, their "
    "confirmed profile facts, and the job description. The description and the notes are what "
    "THE COMPANY wrote: the person may refer to them ('your ad mentions no-shows') but never "
    "says their lines as their own. Never add a fact, a claim or a feeling that is not there. "
    "Plain words, no cliches, no exclamation marks. Name the company and the role exactly as "
    "given. When the person said 'we', keep 'we'. Every sentence carries the id of the one "
    "source it comes from; a sentence that would need something not listed is not written. "
    "Answer with JSON only."
)


# ---------------------------------------------------------------- helpers

def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _short(text: str, n: int = 90) -> str:
    text = _clean(text)
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _block(text: str) -> str:
    text = (text or "").strip()
    return f'"""\n{text[:MAX_TEXT]}\n"""' if text else "(nothing yet)"


def asker(cfg: dict[str, Any], budget: Any = None) -> Ask:
    """Bind the person's model choice and the day's budget into one (system, prompt, schema)
    callable. ``ask_json`` is looked up at call time so tests can swap the model out."""

    def ask(system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return ask_json(cfg["kind"], cfg["model"], system, prompt, schema,
                        base_url=cfg.get("base_url"), api_key=cfg.get("api_key", ""),
                        budget=budget)

    return ask


def plain_error(exc: Exception) -> str:
    """What the page says when a model call fails — in words, never a traceback."""
    if isinstance(exc, BudgetExhausted):
        return BUDGET_MESSAGE
    if isinstance(exc, StructuredError):
        return str(exc)
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def sources(ctx: Context) -> dict[str, str]:
    """Every id a draft may cite, with its text: the only material allowed in."""
    out: dict[str, str] = {}
    for t in ctx.answers():
        out[f"answer:{t['n']}"] = _clean(t["answer"])
    for a in ctx.kept_angles():
        out[f"angle:{a['n']}"] = _clean(a.get("final") or a.get("text"))
    for s in ctx.stories:
        out[f"story:{s['id']}"] = _clean(s.get("text"))
    facts = []
    if ctx.profile.get("name"):
        facts.append(f"name: {ctx.profile['name']}")
    if ctx.profile.get("links"):
        facts.append("links: " + ", ".join(ctx.profile["links"]))
    out["profile"] = "; ".join(facts) or "(no confirmed facts: greet and sign off without a name)"
    if ctx.description.strip():
        out["description"] = ctx.description.strip()[:MAX_TEXT]
    return out


def labels(ctx: Context) -> dict[str, str]:
    """A short, plain label per source id — the why panel reads these."""
    out = {}
    for sid, text in sources(ctx).items():
        if sid.startswith("answer:"):
            out[sid] = f"your answer {sid[7:]}: “{_short(text)}”"
        elif sid.startswith("angle:"):
            out[sid] = f"angle {sid[6:]} you kept: “{_short(text)}”"
        elif sid.startswith("story:"):
            out[sid] = f"your story: “{_short(text)}”"
        elif sid == "profile":
            out[sid] = "your confirmed profile"
        else:
            out[sid] = "the job description"
    return out


def _norm_source(s: Any) -> str:
    s = _clean(s).lower().strip("“”\"'`")
    s = re.sub(r"\s*:\s*", ":", s)
    s = re.sub(r"^(answer|angle|story)\s+", r"\1:", s)
    return s


# ---------------------------------------------------------------- the turn

def _turn_prompt(ctx: Context) -> str:
    head = f"Job: {ctx.title} at {ctx.company}"
    where = [x for x in (f"status: {ctx.status.replace('_', ' ')}" if ctx.status else "",
                         f"so far: {ctx.timeline}" if ctx.timeline else "") if x]
    lines = [head + (f" ({'; '.join(where)})" if where else "")]
    lines += ["", "Job description:", _block(ctx.description)]
    lines += ["", "What the person has read about the company:", _block(ctx.notes)]
    if ctx.cv_label or ctx.cv_facts:
        lines += ["", f"The CV they use for this job: {ctx.cv_label or 'their CV'}. The reader "
                      "will have seen it, so ask past these lines:"]
        lines += [f"- {f['claim']}" + (f" ({f['source']})" if f.get("source") else "")
                  for f in ctx.cv_facts[:12]]
    if ctx.stories:
        lines += ["", "Stories the person has confirmed (id: text):"]
        lines += [f"- story:{s['id']}: {_clean(s['text'])}"
                  + (" [chosen by the person for this message]" if s["id"] in ctx.chosen else "")
                  for s in ctx.stories]
    lines += ["", "The conversation so far:"]
    if not ctx.transcript:
        lines.append("(nothing yet — this is the first question)")
    for t in ctx.transcript:
        lines.append(f"Q{t['n']}: {t['question']}")
        if t.get("skipped"):
            lines.append(f"A{t['n']}: (skipped)")
        elif t.get("answer"):
            lines.append(f"A{t['n']} (id answer:{t['n']}): {_clean(t['answer'])}")
    if ctx.angles:
        lines += ["", "Angles proposed so far (never propose the same one twice):"]
        lines += [f"- angle {a['n']}: {_clean(a.get('final') or a['text'])} — "
                  f"{a.get('verdict') or 'not decided yet'}" for a in ctx.angles]
    lines += ["", f"Tone the person wants: {ctx.tone}"]
    lines += ["", "Shape of a good answer — this one is for a bakery job, not this job: "
              "{\"question\": \"Have you ever run an early-morning shift?\", \"why\": "
              "\"because their ad is about 5am starts\", \"angle\": \"\", \"angle_from\": "
              "\"\", \"enough\": false}"]
    if ctx.answers():
        lines.append("If one of the answers or stories meets a line in the description, give "
                     "the angle now: one sentence to the person, and which id supports it.")
    asked = [_clean(t["question"]) for t in ctx.transcript]
    if asked:
        lines += ["", "Already asked — never ask these again, not even reworded:"]
        lines += [f"- {q}" for q in asked]
    lines += ["", "Questions to draw from — pick one not yet asked and phrase it for this job:"]
    lines += [f"- {q}" for q in QUESTIONS if _qkey(q) not in {_qkey(a) for a in asked}]
    lines.append("")
    if ctx.wants_more:
        lines.append("The person asked for one more question even if there is enough: ask it, "
                     "and set enough to false.")
    else:
        lines.append("Ask the next question now, or set enough to true if there is already "
                     "material for a specific message.")
    return "\n".join(lines)


def _angle_sources(ctx: Context) -> set[str]:
    return {f"answer:{t['n']}" for t in ctx.answers()} | {f"story:{s['id']}" for s in ctx.stories}


def _qkey(q: str) -> str:
    return " ".join(re.findall(r"[a-z]+", (q or "").lower())[:4])


def _fallback_question(ctx: Context) -> str:
    asked = {_qkey(t["question"]) for t in ctx.transcript}
    for q in QUESTIONS:
        if _qkey(q) not in asked:
            return q
    return "Is there anything else you want them to know?"


_META = ("the person", "set enough", "we can ", "suggest a message", "if the person",
         "this angle", "the user", "the candidate", "confirm a story")


def _valid_angle(text: str) -> bool:
    """A real angle is one sentence to the person — not a note the model wrote to itself."""
    low = text.lower().strip()
    if not 20 <= len(text) <= 240 or re.fullmatch(r"angle \d+", low):
        return False
    return not any(m in low for m in _META)


_QWORDS = ("what", "why", "how", "which", "who", "when", "where", "have", "has", "do", "does",
           "did", "is", "are", "can", "could", "would", "will", "tell", "describe", "was")


def _looks_like_question(q: str) -> bool:
    low = q.strip().lower()
    return low.endswith("?") or low.split(" ")[0].rstrip(",") in _QWORDS


def _short_why(why: str) -> str:
    why = re.sub(r"^(this question is asked|i ask this|asked)\s*(because|to|so that)?\s*",
                 "", why, flags=re.I).strip()
    words = why.split()
    return " ".join(words[:16]).rstrip(",;:") if words else ""


def next_turn(ctx: Context, ask: Ask) -> dict[str, Any]:
    """One structured call: the next question, its one-line why, maybe an angle, and
    whether there is enough. The angle is kept only when it names a real answer or story."""
    out = ask(TURN_SYSTEM, _turn_prompt(ctx), TURN)
    question, why = _clean(out.get("question")), _short_why(_clean(out.get("why")))
    if why.endswith("?") or why.lower() in {"true", "false", "none", "null"} \
            or "5am" in why.lower() or "bakery" in why.lower():
        why = ""  # the model put the wrong thing in the box, or copied the example
    if question and (_qkey(question) in {_qkey(t["question"]) for t in ctx.transcript}
                     or not _looks_like_question(question)):
        question = _fallback_question(ctx)  # repeated itself, or answered instead of asking
    angle = None
    text, src = _clean(out.get("angle")), _norm_source(out.get("angle_from"))
    if text and _valid_angle(text) and src in _angle_sources(ctx) and \
            text.lower() not in {_clean(a["text"]).lower() for a in ctx.angles}:
        angle = {"text": text, "from": src}
    enough = bool(out.get("enough")) and bool(ctx.answers()) and not ctx.wants_more
    if enough:
        question = ""
    elif not question:
        question = _fallback_question(ctx)  # the flow never stalls on an empty reply
    return {"question": question, "why": why, "angle": angle, "enough": enough}


# ---------------------------------------------------------------- compose

def _limit_line(channel: str) -> str:
    spec = CHANNELS[channel]
    if "words" in spec:
        lo, hi = spec["words"]
        return f"Length: {lo} to {hi} words."
    return f"Length: under {spec['chars']} characters."


def _compose_prompt(ctx: Context, channel: str, tone: str) -> str:
    spec = CHANNELS[channel]
    lines = [f"Write a {spec['label']} from the person to {ctx.company} about the role "
             f"\"{ctx.title}\".", _limit_line(channel), f"Tone: {TONES[tone]}.",
             f"Name the company exactly as \"{ctx.company}\" and the role exactly as "
             f"\"{ctx.title}\".", ""]
    lines.append("Material — the only things you may use, each with its id:")
    for sid, text in sources(ctx).items():
        if sid == "description":
            lines += ["description —", _block(text)]
        else:
            lines.append(f"{sid} — {text}")
    lines += ["", "Shape, in this order: a greeting ('Hello,' on its own line, never the company's "
              "name); which role, named exactly; one specific reason of interest taken from "
              "the description or the notes, said in the person's own voice ('your ad "
              "mentions…'); what they did that matters here, in their own words; one light "
              "ask; " + ("a short sign-off and the person's name, each on its own line."
                         if channel == "email" else "no sign-off."),
              "", "An example for a different job, to show the shape and the length:",
              EXAMPLE_MESSAGE, "",
              "Now write this person's message, using only the material above."]
    return "\n".join(lines)


_SPLIT = re.compile(r"(?<=[.!?;])\s+")


def split_sentences(text: str) -> list[str]:
    """Lines stay lines (greeting, sign-off, name); each line splits into sentences."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        out += [part.strip() for part in _SPLIT.split(line) if part.strip()]
    return out


def _attribution_prompt(ctx: Context, sentences: list[str]) -> str:
    lines = ["Material, each with its id:"]
    for sid, text in sources(ctx).items():
        lines.append(f"{sid} — {_short(text, 600) if sid == 'description' else text}")
    lines += ["", "The message, sentence by sentence:"]
    lines += [f"{n}. {t}" for n, t in enumerate(sentences, 1)]
    lines += ["", "For each number give the one source id, or an empty string."]
    return "\n".join(lines)


def _attributed(ctx: Context, text: str, ask: Ask) -> dict[str, Any]:
    sentences = split_sentences(text)
    if not sentences:
        return {"draft": "", "sources": []}
    att = ask(ATTRIBUTION_SYSTEM, _attribution_prompt(ctx, sentences), ATTRIBUTION)
    by_n = {}
    for item in att.get("sentences") or []:
        if isinstance(item, dict):
            try:
                by_n[int(item.get("n"))] = _norm_source(item.get("source"))
            except (TypeError, ValueError):
                continue
    rows = [{"sentence": t, "source": by_n.get(n, "")} for n, t in enumerate(sentences, 1)]
    return {"draft": join_sentences(rows), "sources": rows}


def _repair_prompt(ctx: Context, channel: str, draft: str, issues: list[str]) -> str:
    lines = ["Rewrite this message fixing exactly these problems:"]
    lines += [f"- {i}" for i in issues]
    lines += ["", "Keep what is right; use only the material listed. " + _limit_line(channel),
              "", "Current message:", draft, "", "Material, each with its id:"]
    for sid, text in sources(ctx).items():
        lines.append(f"{sid} — {_short(text, 500) if sid == 'description' else text}")
    return "\n".join(lines)


def _is_signoff(text: str) -> bool:
    return bool(re.match(r"^(best|thanks|thank you|regards|kind regards|all the best)\b",
                         text.strip().lower()))


def join_sentences(sentences: list[dict[str, str]]) -> str:
    """Sentences back into a message: a greeting or sign-off gets its own line."""
    parts: list[str] = []
    for s in sentences:
        t = _clean(s.get("sentence"))
        if not t:
            continue
        if not parts:
            parts.append(t)
        elif parts[-1].endswith(",") or t.endswith(",") or _is_signoff(t):
            parts.append("\n" + t)
        else:
            parts.append(" " + t)
    return "".join(parts)


def compose(ctx: Context, channel: str, tone: str, ask: Ask) -> dict[str, Any]:
    """Two small calls: the message as prose, then where each sentence came from.
    Disallowed sentences are removed; one repair pass runs when the checks find problems."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel '{channel}'")
    if tone not in TONES:
        raise ValueError(f"unknown tone '{tone}'")
    text = _clean_message(ask(COMPOSE_SYSTEM, _compose_prompt(ctx, channel, tone), DRAFT))
    cleaned = clean(_attributed(ctx, text, ask), ctx, channel)
    left = [i for i in cleaned["issues"] if not i.startswith("removed ")]
    if left and cleaned["sources"]:
        again = _clean_message(ask(COMPOSE_SYSTEM,
                                   _repair_prompt(ctx, channel, cleaned["draft"], left), DRAFT))
        repaired = clean(_attributed(ctx, again, ask), ctx, channel)
        if len(repaired["issues"]) < len(cleaned["issues"]) and repaired["sources"]:
            return repaired
    return cleaned


def _clean_message(out: dict[str, Any]) -> str:
    text = str(out.get("message") or "")
    return re.sub(r"\n{3,}", "\n\n", text.strip())


# ---------------------------------------------------------------- checks

_WE = re.compile(r"\b(we|our|ours|us)\b", re.I)
_I = re.compile(r"\b(I|my|mine)\b")


def _length_issue(draft: str, channel: str) -> str:
    spec = CHANNELS[channel]
    if "words" in spec:
        lo, hi = spec["words"]
        n = len(draft.split())
        if n < lo:
            return f"too short for an {spec['label']}: {n} words ({lo} to {hi})"
        if n > hi:
            return f"too long for an {spec['label']}: {n} words ({lo} to {hi})"
        return ""
    n = len(draft)
    if n >= spec["chars"]:
        return f"too long for a {spec['label']}: {n} characters (under {spec['chars']})"
    return ""


def clean(result: dict[str, Any], ctx: Context, channel: str) -> dict[str, Any]:
    """The deterministic checks. Unsourced sentences go; the rest is reported in plain words.
    Returns {"draft", "sources", "issues"} — the draft rebuilt from what was kept."""
    allowed = sources(ctx)
    sentences = list(result.get("sources") or [])
    if not sentences and _clean(result.get("draft")):
        sentences = [{"sentence": _clean(result["draft"]), "source": ""}]
    kept, issues = [], []
    for s in sentences:
        text, src = _clean(s.get("sentence")), _norm_source(s.get("source"))
        if not text:
            continue
        if not src:
            issues.append(f"removed a sentence with no source: “{_short(text)}”")
        elif src not in allowed:
            issues.append(f"removed a sentence whose source is not allowed ({src}): "
                          f"“{_short(text)}”")
        else:
            kept.append({"sentence": text, "source": src})
    draft = join_sentences(kept)
    if not draft:
        issues.append("nothing left to say — answer a question or two first")
        return {"draft": "", "sources": [], "issues": issues}
    if (length := _length_issue(draft, channel)):
        issues.append(length)
    if ctx.company and ctx.company not in draft:
        issues.append(f"the company name is missing — spell it exactly as in the job: "
                      f"{ctx.company}")
    if ctx.title and ctx.title not in draft:
        issues.append(f"the role title is missing — spell it exactly as in the job: {ctx.title}")
    low = draft.lower()
    issues += [f"banned phrase: “{p}”" for p in BANNED if p.lower() in low]
    for x in result.get("sources") or []:
        if re.match(r"^(we need|we are looking|we're looking|our team needs|you will own)",
                    x.get("sentence", "").strip(), re.I):
            issues.append("written in the company's voice: “" + _short(x["sentence"], 60) + "”")
    if "!" in draft:
        issues.append("no exclamation marks")
    for s in kept:
        origin = allowed.get(s["source"], "")
        if s["source"] in ("description", "profile"):
            continue  # the company's "we" is not the person's group work
        if _WE.search(origin) and _I.search(s["sentence"]) and not _WE.search(s["sentence"]):
            issues.append(f"group work stays “we”: “{_short(s['sentence'])}” comes from "
                          "something you told as “we”")
    return {"draft": draft, "sources": kept, "issues": issues}


def apply_checks(result: dict[str, Any], ctx: Context, channel: str) -> tuple[str, list[str]]:
    out = clean(result, ctx, channel)
    return out["draft"], out["issues"]


def check(result: dict[str, Any], ctx: Context, channel: str) -> list[str]:
    return clean(result, ctx, channel)["issues"]
