# Mortal

A story builder where one person lives in a tiny ASCII town, in real time.

```
GOD (you)  ──words, mood, objectives──▶  DEMI-GOD (an LLM)  ──decisions, events──▶  MORTAL
   ▲                                                                                  │
   └──────────────── web UI: live ASCII scene, chronicle, gauges ◀───────────────────┘
```

* **You are God.** You describe the person (characteristics, temperament, people around them),
  then speak to the demi-god while the life unfolds: an **event** ("a letter arrives from an old
  friend") or a **nudge** ("a thought: visit the temple").
* **The demi-god is an LLM** whose **mood** and **objectives** you set and change at any time.
  It makes every other decision in the person's life: the day's plan, how an encounter goes,
  how a crisis is met, and how your words come to pass. A wrathful demi-god and a benevolent one
  carry out the same decree very differently.
* **The Mortal** has traits, needs (energy, hunger, social, fun, health, mood, money), memories
  and relationships. Cheap local simulation runs between decisions, so the LLM is consulted a
  handful of times per simulated day rather than every tick.

## Quick start

```bash
pip install -e ".[dev]"          # Python 3.11+ (developed and tested on 3.13)
mortal serve                      # http://127.0.0.1:8000
```

Open the page, describe a person, press **Breathe life into them**, then **play**.
With no API key the **offline rule-based demi-god** runs the life, so everything works at once.

Headless: `mortal simulate --days 3 --mood wrathful --say "Ayo loses his job"` prints the story.

### Choosing the demi-god's brain

| Backend | How to enable | Notes |
|---|---|---|
| Offline rule-based | nothing (default fallback) | Deterministic per seed. Understands god's words by keyword only. |
| Gemini | `export GEMINI_API_KEY=...` (`MORTAL_GEMINI_MODEL`, default `gemini-2.5-flash`) | REST, JSON output mode. |
| Ollama / any OpenAI-compatible | `MORTAL_OPENAI_BASE_URL=http://localhost:11434/v1`, `MORTAL_OPENAI_MODEL=llama3.1` | Optional `MORTAL_OPENAI_API_KEY`. Retries without JSON mode if the server rejects it. |

"Auto" picks Gemini if a key is set, else an explicitly configured local endpoint, else offline.
You can switch backend per life, live, from the UI. If a model fails (network, bad JSON), the
offline demi-god covers that decision; after two failures in a row the model is skipped for
five decisions so a dead endpoint costs one timeout, not one per tick.

Other settings: `MORTAL_DATA_DIR` (default `./mortal_data`), `MORTAL_LLM_TIMEOUT` (30 s),
`MORTAL_MAX_DECISIONS_PER_DAY` (8 automatic LLM calls per day; dawn plans and god's words are
never rationed), `MORTAL_FATE_EVENTS_PER_DAY` (1.0; 0 disables random twists),
`MORTAL_GEMINI_BASE_URL`.

## How a life runs

One tick = 10 simulated minutes; a story day runs 06:00 → 06:00 (144 ticks). The speed control
sets ticks per second (default 2: a day in about 70 seconds).

The demi-god is called at **decision points**:

| Trigger | When | It decides |
|---|---|---|
| `dawn` | every morning | the whole day's schedule, a short omen, an updated life summary |
| `divine` | the moment God speaks (even while paused) | how the words come to pass, in its own mood |
| `crisis` | hunger, exhaustion, health, debt or mood becomes critical | how the person responds |
| `encounter` | the person shares a place with someone they have not seen lately | what passes between them |
| `fate` | at random, about once a day | one small twist of fortune; may introduce a newcomer |
| `death` | health reaches zero, or God decrees it | the epitaph |

A decision is a bounded JSON object (narration, thought, effects on gauges, relationship changes,
new people, a schedule, a memory worth keeping, a change of livelihood, an optional death).
LLM replies are parsed leniently, then clamped: effects are capped, unknown places or kinds are
dropped, and a reply that is unusable falls back to the rule-based demi-god.

Between decisions the engine follows the schedule: it walks the person along the ASCII streets
(6 cells per tick), applies per-tick body and money effects, and moves the townsfolk. An
interruption's schedule replaces only the span it covers, then the person **resumes** what they
were doing. Survival reflexes (collapsing from exhaustion, eating when starving) keep a neglected
plan from killing the person; in practice a life ends by decree (God's, carried out by the demi-god).

## Architecture

```
cli ──────┐   ┌────── api   FastAPI: JSON control + SSE stream + static UI
          ▼   ▼
          engine    tick loop, demi-god service, prompts, rule demi-god, runner, registry, store
        ┌───┴───┐
      core     llm  models/decision/plan/townmap/clock  |  provider protocol, Gemini, OpenAI-compatible
        └───┬───┘
     errors · config   (leaf modules)
```

Imports point downwards only; `tests/test_architecture.py` parses every module and fails if a layer
reaches upward. The UI (`web/static`) is vanilla JS with no build step and talks only to the API.

* **Concurrency.** One `asyncio.Lock` per life serialises mutations (a tick, or processing God's
  words). Viewers are woken by an `Event` and build their own frame from current state with a
  private log cursor, so a slow browser never stalls the simulation or loses a chronicle line.
  LLM calls run in a worker thread; time pauses while the demi-god deliberates (the UI says so).
* **Persistence.** Each life is one JSON file, written atomically at dawn, on pause and on every
  setting change. Restarting the server restores lives paused. Randomness is re-seeded every dawn from
  `(seed, day)` (and on restore from `(seed, tick)`), so restoring does not break reproducibility.
* **Determinism.** With the offline demi-god, the same seed and the same words in the same order
  produce the identical story. LLM-driven lives are not reproducible (model output varies).

### HTTP API

| Method and path | Purpose |
|---|---|
| `GET /api/meta` | places, kinds, mood presets, backends |
| `GET /api/lives` · `POST /api/lives` | list · create (`person`, `npcs`, `god`, `llm`, `seed`) |
| `GET /api/lives/{id}` · `DELETE` | frame snapshot · delete |
| `POST /api/lives/{id}/play` `/pause` `/step` · `PUT .../speed` | time controls |
| `POST /api/lives/{id}/god` | `{kind: "event" \| "nudge", text}` |
| `PUT /api/lives/{id}/demigod` | `{mood, objectives[]}` |
| `PUT /api/lives/{id}/llm` | `{provider, model}` |
| `GET /api/lives/{id}/stream` | Server-Sent Events: one frame per change |
| `GET /api/lives/{id}/export` | full state as JSON |

## Extending

* **New backend:** implement `Provider.complete(system, user) -> str` in `llm/`, register it in `llm/factory.py`.
* **New place:** add it to `core/constants.py` and `core/townmap.py` (a building or an open-air anchor).
* **New activity kind:** add it to `KINDS`, `KIND_DELTAS` (physics) and `KIND_PHRASE`.
* **Tests:** `pytest` (100 tests; no network needed).

## Known limits
* The offline demi-god reads God's words by keyword. "Ayo loses his job" changes his livelihood;
  subtler phrasing needs a real model.
* Single process, in-memory runners, no authentication: bind to localhost (the default) or put
  it behind your own access control. God's text is passed to the model as-is, so treat God as a
  trusted user; model output is validated and bounded before it touches the simulation.
* The town is one fixed map. Characters are single glyphs; the scene is intentionally simple.
