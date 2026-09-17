# Message session — design

Status: design agreed in outline, nothing implemented.
An earlier design matched CV facts to job-description needs. That was paper
matching paper. This design keeps its good parts and replaces the middle.

## The idea in one line

Presence does not write from documents; it interviews the person for a few
minutes, proposes angles, and writes from what they said.

A recruiter is not impressed that a skill on the CV matches a word in the ad.
They are impressed by something they could not have read off the CV: a specific
observation about the product, a small thing the person actually tried, an honest
reason, a good question. None of that is on paper. So the session goes looking
for it, and the person decides what goes in.

## From the person's side

1. On any job row: **Prepare a message**. Before applying (first impression) or
   after (follow-up); email, LinkedIn message, or connection note.
2. The session opens as its own page. At the top, what Presence has for this job:
   the description (or a box to paste it), a box for anything else the person wants
   Presence to know about the company — a page they read, a post, a product note,
   pasted in their own time — the CV that was or will be sent, and any stories from
   the bank that look relevant, as chips to accept or skip.
3. Presence asks one question at a time. The person types. Any question can be
   skipped. Presence stops when it has enough, usually after three to six; the
   person can press **that's enough** earlier or **ask me more** past the cap.
4. As answers come in, Presence may propose an angle: *"You mentioned rebuilding
   the reporting pack — that meets their 'own the monthly numbers' line. Use it?"*
   Accept, edit, or reject. A rejected angle never appears in a draft.
5. Pick the channel and tone; Presence writes. Beside the draft, a **why** panel
   maps every sentence to its source: a line the person typed, an angle they
   accepted, or a confirmed fact. Nothing else is allowed in.
6. Copy · Edit · Save the new stories to the bank · Mark as sent. Presence never
   sends anything.

Text only for now. No reminders or timing rules in this update.

## The questions

Seeded by the description and the CV, aimed past both. The pool, from which the
model picks and phrases per job:

- Why this one, honestly?
- Have you done anything that touches their problem, even small, even outside work?
- What did you notice about them — the product, a post, the way the ad is written?
- What would you do in your first month, or what would you ask them?
- Is there a story you tell about yourself that fits here?
- How do you want to sound?

## Stories bank

Everything the person tells Presence about themselves is kept, in their words,
so the third message is faster than the first.

- A story is `{id, text, tags, source, job_id, created, confirmed}`. Source is
  `session` (confirmed by construction: they typed it) or `cv` (drafted from the
  model read of a CV; **unconfirmed until the person ticks it** — charter rule 2).
- Lives in the data folder as `stories.json`. Shown in setup step 1 beneath the CV
  library as **Your stories**: add, edit, delete, confirm. Offered in every session
  as chips. This replaces the old design's "facts list".
- The CV read that already exists seeds the bank: each work or project highlight
  becomes a draft story the person can keep or drop.

## Where company knowledge comes from

Presence itself fetches nothing about a company. Two sources, both human-driven:

1. The job description, from the board's published API (captured at ingest; the
   connectors return it today and drop it — that is fixed in this update) or pasted.
2. Text the person pastes into the session page: a company page they read, a post,
   a news item, a product note. It is kept with the job as company notes and can be
   added to at any time.

No browser extension. A paste box is enough, it works for every site including the
ones whose terms forbid add-ons, and it never asks the person to install anything.

## What is written, and how

- **Session turn**: one structured call — `{question, why_asking, proposed_angle?,
  enough}` — on the person's model through the single `/v1` transport, with the
  daily budget charged. Small local models handle this; the turn is short.
- **Compose**: one call with the accepted angles, the person's answers, the confirmed
  facts, the channel and tone. Two variants: plain and a little warmer.
- **Checks, deterministic**: length per channel (email 90–140 words, LinkedIn
  message under 600 characters, connection note under 280), company and role spelled
  as in the description, banned phrases ("I am writing to", "I believe I would be a
  great fit", "passionate", "leverage", "synergy", "excited to", exclamation marks),
  and every sentence carrying a source. A sentence without one is removed, not
  softened. Group work stays "we".
- **Why panel**: sentence → source line, shown beside the draft.

## Data

- `jd/<job_id>.txt` — the description, fetched or pasted.
- `company/<job_id>/<n>.json` — a pasted note: title (optional), text, added_at.
- `sessions/<job_id>/<date>.json` — transcript, angles with their verdicts, drafts.
- `stories.json` — the bank.
- One neutral event kind, `messaged`, rendered in the timeline as `messaged 23 Aug`
  and changing no status. Adding it also fixes the existing round-trip bug: an
  unrecognised timeline label currently becomes an *assessment* event and moves the
  job's status.

## Build order

1. **Foundations**: description capture from all five boards and the paste box;
   the `messaged` event and the label fix; the daily budget wired into every model
   call; the model transport unified on `/v1` so the session runs on Ollama, a
   person's own server, or an API alike.
2. **Stories bank**: seeded from the CV read, confirmed in setup step 1, manual add.
3. **The session**: panel on the job row, questions, angles, compose, checks, why
   panel, actions, the LinkedIn cuts.
Each step is usable on its own and tested without the network, in the house style.

## Out of scope, on purpose

Sending. Voice. Reminders and timing. Fetching company pages by Presence itself.
Reading LinkedIn by any means. A browser extension. Form filling. The interview
thank-you.
