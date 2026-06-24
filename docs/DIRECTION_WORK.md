# Trellis Work — Direction

**Status:** Concept / future fork

---

## What This Is

A fork of Trellis oriented around work rather than personal health. Same core architecture — oracle, assembler, domain routing, tool pattern — different domains.

The problem it solves: knowledge and context scattered across Word docs, Figma files, meeting notes, call recordings, Slack threads. No single place. No synthesis. Hard to query later.

The goal: one system that ingests everything, synthesises it, and lets you query across it in natural language. You don't write — you capture and upload. The system writes.

---

## Primary Use Case

Single user. Not a team tool (yet). Plugged into a CLI or terminal (Warp, etc.) rather than Telegram, though Telegram could stay as a mobile interface.

Domain knowledge tracking is the anchor use case: take a complex domain (e.g. eCOA — electronic clinical outcome assessments), feed it everything — meeting notes, calls, regulatory documents, internal docs, Figma files — and have the system maintain a living knowledge base that you can query, update, and navigate.

---

## Core Capabilities Needed

### Capture and Ingestion
- Text input (same as Trellis today)
- Document upload: PDF, Word, plain text
- Figma via API
- Future: call transcripts, email threads

### Synthesis
- Same model as Trellis captures: raw stored, synthesis surfaced
- On ingest: extract key concepts, decisions, open questions, relationships
- On update: diff against existing synthesis, not a full rewrite

### Knowledge Structure
- Projects as top-level containers
- Domains within projects (e.g. "eCOA", "Regulatory", "Design")
- Threads within domains — same pattern as the learn module
- Decisions, stakeholders, open questions as first-class objects

### Query
- Natural language queries across everything: "what did we decide about X", "what's the current state of Y", "what's still open on Z"
- Requires RAG (retrieval-augmented generation) for large document sets — chunking + embedding + similarity search before oracle call

### Interfaces
- **CLI / terminal**: primary for deep work
- **Telegram**: mobile / quick capture
- **UI**: Notion-style tree view, longer term — projects > domains > threads > entries

---

## What Carries Over From Trellis

- Oracle / assembler / registry pattern — unchanged
- Domain routing — same classifier, different signals
- Capture model (raw + synthesis) — same
- Learn module pattern — maps directly to domain knowledge threads
- Tool-as-API-surface — CLI and UI call the same tools

## What's New

- RAG layer for large document search
- File parsers (PDF, Word, Figma)
- Project / domain / stakeholder data models
- Richer tree structure in the knowledge layer
- CLI transport layer (alongside or instead of Telegram)

---

## Build Order (when the time comes)

1. Fork Trellis, strip health/training domains
2. Add project + domain models
3. Wire learn module as the knowledge thread engine
4. Add PDF/Word ingestion with chunking
5. Add embedding store + RAG query layer
6. CLI interface
7. Figma ingestion
8. UI (tree view — Notion-style)
