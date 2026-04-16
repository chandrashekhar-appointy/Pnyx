"""
Chat service package - Production-grade RAG pipeline.

Splits the monolithic ChatService into:
- IntentClassifier: Active topic detection + scope classification  
- EvidenceRetriever: Tiered evidence retrieval with confidence gating
- ChatService: Main orchestrator

This __init__.py re-exports ChatService for backward compatibility.
"""

from .service import ChatService

__all__ = ["ChatService"]
