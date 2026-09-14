# Follow-up writer — design

Status: design, nothing implemented. Branch `followup-writer`. 14 Sep 2026.

## What it is

After a person applies, Presence drafts the follow-up they would otherwise never
get round to: an email to the recruiter or hiring manager, or a short LinkedIn
message. The draft is relevant to *that* employer because it is built from the
full job description, and it mentions what the person can do only where there
is an honest, evidenced bridge between the job's needs and something they did.

Presence never sends it. The person reads, edits, copies, sends, and ticks
"sent". That is charter rule 1 and it does not bend here.

## Why a template will not do

A follow-up that reads "I am writing to follow up on my application… I believe
my skills in Python, SQL and machine learning make me a strong fit" is worse
than silence: it tells the reader the sender did not think about them. The
failure is structural, not stylistic. Three things make it artificial:

1. It repeats the CV instead of answering the job description.
2. It lists skills instead of showing one thing the person did.
3. It has the same shape every time, so a recruiter who receives many
   recognises it instantly.

So the design is not "a mail writer with a skills slot". It is a small pipeline
whose middle step may decide that the best message mentions no skills at all.

## Inputs

| Input | Where it comes from | Notes |
|---|---|---|
| Job record | tracker | company, role, link, status, timeline, notes, next action |
| Full job description | the board's published API (Greenhouse, Lever, Ashby, Workable, SmartRecruiters all return the description in the job endpoint), else pasted by the person | stored once per job as `data/jd/<job_id>.txt`; LinkedIn postings are always pasted, never fetched |
| Person's facts | profile: identity, skills, work authorisation, plus a new **facts** list | each fact = claim + evidence + tags, drafted from the CV at setup and confirmed by the person; this is the only grounding corpus |
| Situation | timeline events | applied N days ago with no reply · assessment done · interview done · offer pending |
| Recipient | the person, or a name/email that appears in the job description or the confirmation email | Presence does not look people up; LinkedIn stays manual |
| Channel | email or LinkedIn | sets length and register |
| Voice sample | 3–5 sentences the person wrote themselves (optional) | keeps the draft in their register, not the model's |

## Pipeline

**1 · Extract** (per job, cached). From the job description: role in one line,
must-have needs, nice-to-haves, team or product context, culture cues, a named
contact if present, process signals ("we reply within two weeks"). A small
local model can do this; the output is a short structured note the person can
see and correct.

**2 · Match.** Intersect the needs with the person's facts. A candidate bridge
is `(need, fact, evidence, strength)`. Rules:

- A bridge needs **evidence** (a project, a result, an experience), not a
  matching keyword. "Python" against "Python" is not a bridge; "built the
  reporting pipeline that cut monthly reporting time by 40%" against "automate
  reporting" is.
- Keep at most **two**, usually one. Rank by specificity to *this* description.
- If nothing clears the bar, the result is **no bridge**, and that is a valid
  outcome: the message becomes a plain, courteous check-in with one genuine
  sentence of interest drawn from the description. No padding.

**3 · Compose.** One model call with a tight brief:

- Shape, at most five moves: context (what and when) · one specific reason of
  interest taken from the description or company, never generic · the bridge
  sentence with its evidence, if any · a light ask (timeline, anything else
  they need) · sign-off.
- Length: email 90–140 words; LinkedIn message under 600 characters;
  connection note under 280.
- Register: the person's voice sample if given; otherwise plain and warm.
- Banned: "I am writing to", "I believe I would be a great fit", "passionate",
  "leverage", "synergy", "excited to", exclamation marks, flattery about the
  company, and any restating of the CV.
- Two variants: plain, and slightly warmer. Plus the LinkedIn cut.

**4 · Verify** (deterministic where possible, model critic where not).

- Grounding: every factual claim in the draft maps to a fact id. Unsupported
  claims are removed, not softened. Group work stays "we".
- Names and role title spelled as in the description; dates match the
  timeline.
- Length and banned-phrase checks.
- A "why this draft" panel: which lines of the description and which facts
  were used. The draft shows its work; that is the brand.

**5 · Deliver.** The draft appears on the job's row in the tracker and, if
paired, as a Telegram message. Actions: Copy · Edit · Mark as sent · Snooze ·
Not for this job. "Mark as sent" records a `followed up` event so the timeline
reads `13 Aug → followed up 23 Aug → rejected 26 Aug`. Nothing is sent by
Presence.

## When to follow up

The writer and the timing belong together; a draft at the wrong moment is
noise. Defaults, all editable in the pack:

| Situation | Default moment | Message |
|---|---|---|
| Applied, no reply | day 7–10 | first follow-up |
| Still no reply | day 21 | second and last; then stop |
| Interview done | within 24 h | thank-you with one specific reference to the conversation (the person supplies it) |
| Assessment submitted | only if silent for 10 days | short check-in |
| Rejected or ignored | never | — |
| Company under cooldown | never | — |

The brief announces it: "Acme · 9 days since applying · follow-up draft ready".

## Evaluation before it ships

- A fixture set: ten job descriptions of different kinds (graduate scheme,
  startup engineer, analyst, research), three profiles, and the author's own
  real follow-ups as the golden examples.
- Judges: grounding (zero unsupported claims, hard fail), relevance (at least
  one detail that could only come from this description), naturalness (blind
  pairwise against the golden examples), brevity.
- The no-bridge case is tested explicitly: a mismatched profile must yield a
  clean check-in, not a stretched one.
- Must pass on the default local model (qwen3.5:9b); an API model may do better
  but is not required.

## Pack and editing

`packs/followup/` holds the compose brief, the banned list, the channel limits
and the timing table as plain files. Free, in the core product, editable by
anyone; the same principle as the drills and simulations.

## Data model changes (small)

- `data/jd/<job_id>.txt` — the description text, fetched or pasted.
- `profile.facts[]` — `{id, claim, evidence, tags}`; drafted from the CV,
  confirmed by the person.
- `data/drafts/<job_id>/<date>.json` — drafts with their "why" panel.
- One new event kind, `followed_up`, rendered in the timeline and changing no
  status.

## Out of scope, on purpose

Sending, scheduling sends, finding recruiters' names or addresses, reading
LinkedIn, any message that is not about a job the person applied to.

## Open questions for Denis

1. Facts list: draft it from the CV at setup (the person confirms each line),
   or ask for it fresh in the profile step?
2. Should a thank-you after an interview be part of this writer or a separate,
   simpler one?
3. LinkedIn message vs connection note: support both cuts, or the message only?
