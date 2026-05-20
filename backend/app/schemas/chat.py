from pydantic import BaseModel
from typing import List, Optional, Dict, Union

try:
    from ..model_config import GEMINI_DEFAULT_MODEL
except (ImportError, ValueError):
    import os
    GEMINI_DEFAULT_MODEL = os.getenv("GEMINI_DEFAULT_MODEL", "gemini-3.5-flash")


class ChatRequest(BaseModel):
    meeting_id: str
    question: str
    model: str
    model_name: str
    context_text: Optional[str] = None
    context_entries: Optional[List[Dict[str, object]]] = None
    allowed_meeting_ids: Optional[List[str]] = None  # Scoped search
    history: Optional[List[Dict[str, str]]] = None  # Conversation history


class CatchUpRequest(BaseModel):
    """Request model for catch-up summary"""

    transcripts: List[Union[str, Dict[str, object]]]  # Supports plain text or rich transcript entries
    model: str = "gemini"
    model_name: str = GEMINI_DEFAULT_MODEL
    window_minutes: Optional[int] = None
    window_start_iso: Optional[str] = None
    window_end_iso: Optional[str] = None
    meeting_elapsed_seconds: Optional[int] = None


class SearchContextRequest(BaseModel):
    """Request model for cross-meeting context search"""

    query: str
    n_results: int = 5
    allowed_meeting_ids: Optional[List[str]] = None  # None = search all meetings
