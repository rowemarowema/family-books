"""Backup / restore primitives. Group H.

Modules:
  retention.py — pure-Python retention policy (30 daily + 12 monthly +
                 7 annual per ADR-001 / Batch #1 #3). Testable without
                 DB or B2.
  storage.py   — Backblaze B2 client wrapper (boto3 S3-compatible).
                 Single class so the management commands and tests can
                 mock it.
"""
