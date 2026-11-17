# 0004. PostgreSQL for vectors, search, queue, tenancy and audit

**Status:** accepted · 17 Nov 2026

## Context
A typical GenAI stack adds a vector database, a search engine, a queue and a cache. Each one is another thing for the customer's team to run, secure and back up after we leave.

## Decision
Use PostgreSQL for:
- vectors (pgvector with HNSW indexes)
- keyword search (built-in full-text search)
- the job queue (`FOR UPDATE SKIP LOCKED`)
- tenancy (row-level security keyed on a session setting)
- the audit log (append-only, enforced by a trigger)

Behind a `VectorStore` interface so a dedicated vector database can be swapped in if a customer outgrows it.

**Local development exception:** the PostgreSQL 16 on the build machine (Windows) has no pgvector. Locally, an in-memory NumPy store implements the same interface; CI, Kubernetes and AWS use pgvector. Both pass the same store tests.

## Consequences
- One database to operate. Handover is simpler.
- At very large corpora, a dedicated vector store may win on cost and latency. The interface keeps that door open.
