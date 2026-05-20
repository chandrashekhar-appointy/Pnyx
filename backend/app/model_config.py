"""
Centralized model configuration.

All Gemini model references should import from here so that swapping
models is a single env-var change (GEMINI_DEFAULT_MODEL).
"""

import os

# The one source of truth for the default Gemini model used across:
#   - Notes generation
#   - Transcript processing / summarization
#   - Chat / intent classification
#   - Diarization post-processing
#   - Behavior compiler
#
# Override via environment variable:
#   GEMINI_DEFAULT_MODEL=gemini-3.5-flash
GEMINI_DEFAULT_MODEL: str = os.getenv("GEMINI_DEFAULT_MODEL", "gemini-3.5-flash")
