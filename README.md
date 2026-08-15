# civic-watch

A searchable, speaker-attributed archive of local government meetings, with an
agent that answers questions and cites the moment in the recording.

The first instance is Pasco County, Florida — **[pasco.watch](https://pasco.watch)**
— built from the county's published agendas and minutes and from 1,036 hours of
its own recordings. The codebase is generic; the county is configuration.

## What it holds

| | |
|---|---|
| meetings | 432 recorded, 1,214 in the published record |
| transcript | 298,737 utterances, 1,036 hours |
| retrieval | 167,174 passages, BM25 + pgvector HNSW + curated thread keys |
| published record | 23,122 agenda items, their dispositions and case histories |
| invariants | 50 data checks, run after every rebuild, repairing nothing |

## How it fits together

```
Postgres 18 + pgvector          everything a reader touches
  └─ reader API (web/)          JSON only; retrieval, the tool surface, the agent
      └─ Next UI (ui/)          the reading surfaces, /search, /ask, /admin
          └─ reverse proxy      TLS and the public hostname

GPU workstation                 ASR, diarization, embedding, the ops console
```

The site serves no media — playback is a YouTube iframe — so the recordings
never need to be reachable from the internet.

## Running it

- `deploy/postgres-unraid.md` — the database, by hand
- `deploy/docker-compose.yml` — the API and the UI
- `deploy/nginx-proxy-manager.md` — the edge
- `LAUNCH.md` — the deployment plan and what is still open
- `STATE.md` — the project's memory: what is measured, what is a guess, and
  every gotcha that cost real time

## Documents worth reading before changing anything

`STATE.md` is long on purpose. It records the measurements this project rests
on, and — more usefully — the ones that turned out to be wrong. Two of its
rules generalise past this codebase:

- **Score coverage, never string counts.** Two audits of the redaction detector
  said it was broken; both were the audit.
- **Treat an empty check as a failure to have tested anything**, not as a pass.

## Privacy

Public comment is public record, and people read their home address into it at
the podium. An address in a PDF nobody indexes is not the same exposure as an
address that is the top hit for a search, so `bin/redact.py` proposes removals
and a person decides each one (`/admin/redactions`).

Redaction layers rather than destroys: `utterances.text_raw` is what the
recogniser produced and is never indexed; `utterances.text` is what the archive
publishes. Four invariants prove a removal actually reaches the transcript, the
passages, the search index, and that the raw text still holds what was said.

No real name or address appears anywhere in this repository. Every example is
fabricated and verified at zero occurrences in the corpus, and the labelled
ground-truth files are never committed.

## Not the official record

Pasco County publishes the authoritative agendas and minutes. This archive
mirrors them, adds a transcript layer bound to them, and states its own limits
on every page that has one.
