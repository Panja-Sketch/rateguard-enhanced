"""RateGuard Demo Insurer Rating Engine: an independently deployed, black-box
REST reference engine (locked doc section 8.3).

This package is a sibling of `app` in the repository but is a separate
deployable: it has its own Docker image and requirements, and it must never
import from `app` (enforced by tests/rating_engine/test_isolation.py). RateGuard
knows this service only through the versioned REST connector contract. It
serves two implementation versions, `canonical-v1` ($700.00 for the golden AZ
HO3 case) and `defective-v1` ($655.00 for the same case).
"""
