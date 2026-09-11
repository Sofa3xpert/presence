# The Presence Charter

These are product rules, not etiquette. Each one is enforced by code and
verified by tests; a change that weakens any of them is a breaking change to
the project's identity, not a feature.

## 1. A human clicks every Submit

Presence contains no code path that submits, sends, or posts an application on
a user's behalf. It finds roles, judges fit, prepares material, and reminds —
the final act is always the user's. This also means: no code that defeats
bot detection, CAPTCHAs, or rate limits, anywhere, ever.

## 2. No invented facts

Facts about the user come from two places only: what Presence extracted and
the user **confirmed**, or what the user stated directly. Drafting components
may never invent employers, numbers, skills, or claims. Unconfirmed
extractions are never acted on.

## 3. Data stays home

All personal data — profile, CV, tracker, credentials — lives on the machine
Presence runs on. No telemetry, no analytics phoning home, no cloud account.
Where Presence reads a user's mailbox, access is read-only by construction.

## 4. Spending is capped

A hard daily token-budget cap ships on by default. When the cap is reached,
Presence stops and says so in the user's messenger. The fully-local model path
(Ollama) must always remain a first-class, tested configuration so the whole
loop can run at zero marginal cost.
