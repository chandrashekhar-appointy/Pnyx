import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple

try:
    from .storage import StorageService
except (ImportError, ValueError):
    from services.storage import StorageService


logger = logging.getLogger(__name__)


class DocumentStorageService:
    """Store large transcript and notes payloads in object storage."""

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def transcript_path(meeting_id: str) -> str:
        return f"meetings/{meeting_id}/transcripts/full/latest.json"

    @staticmethod
    def transcript_version_path(meeting_id: str, version_num: int) -> str:
        return f"meetings/{meeting_id}/transcripts/versions/v{version_num}.json"

    @staticmethod
    def summary_path(meeting_id: str) -> str:
        return f"meetings/{meeting_id}/notes/latest.json"

    @staticmethod
    def _preview_text(text: Optional[str], max_len: int = 300) -> str:
        value = (text or "").strip()
        if len(value) <= max_len:
            return value
        return value[: max_len - 3] + "..."

    @classmethod
    async def save_json(
        cls, path: str, payload: Dict[str, Any], public_key: Optional[str] = None
    ) -> Tuple[str, int, str, Optional[Dict[str, Any]]]:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        
        encryption_wrapper = None
        final_path = path
        if public_key:
            from .encryption_service import EncryptionService
            raw, encryption_wrapper = EncryptionService.encrypt_document(raw, public_key)
            if final_path.endswith(".json"):
                final_path = final_path.replace(".json", ".enc.json")

        ok = await StorageService.upload_bytes(
            raw, 
            final_path, 
            content_type="application/json" if not public_key else "application/octet-stream"
        )
        if not ok:
            raise RuntimeError(f"Failed to upload JSON document to storage: {final_path}")
            
        return cls._sha256_bytes(raw), len(raw), final_path, encryption_wrapper

    @staticmethod
    async def load_json(path: Optional[str]) -> Optional[Dict[str, Any]]:
        if not path:
            return None
        raw = await StorageService.download_bytes(path)
        if raw is None:
            return None
        if path.endswith(".enc.json"):
            return {"_is_encrypted_payload": True}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            logger.error("Failed to decode JSON document %s: %s", path, exc)
            return None

    @classmethod
    async def save_full_transcript(
        cls,
        meeting_id: str,
        transcript_text: str,
        *,
        model: str,
        model_name: str,
        chunk_size: int,
        overlap: int,
        public_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        path = cls.transcript_path(meeting_id)
        payload = {
            "meeting_id": meeting_id,
            "text": transcript_text,
            "model": model,
            "model_name": model_name,
            "chunk_size": chunk_size,
            "overlap": overlap,
        }
        sha256, byte_size, final_path, encryption_wrapper = await cls.save_json(path, payload, public_key=public_key)
        return {
            "path": final_path,
            "sha256": sha256,
            "byte_size": byte_size,
            "preview": cls._preview_text(transcript_text, max_len=500),
            "encryption": encryption_wrapper,
        }

    @classmethod
    async def load_full_transcript_text(cls, path: Optional[str]) -> Optional[str]:
        payload = await cls.load_json(path)
        if not payload:
            return None
        return str(payload.get("text") or "").strip() or None

    @classmethod
    async def save_transcript_version(
        cls,
        meeting_id: str,
        version_num: int,
        *,
        source: str,
        content: Any,
        is_authoritative: bool,
        created_by: str,
        alignment_config: Optional[Dict[str, Any]],
        confidence_metrics: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        path = cls.transcript_version_path(meeting_id, version_num)
        payload = {
            "meeting_id": meeting_id,
            "version_num": version_num,
            "source": source,
            "is_authoritative": is_authoritative,
            "created_by": created_by,
            "alignment_config": alignment_config or {},
            "confidence_metrics": confidence_metrics or {},
            "segments": content or [],
        }
        sha256, byte_size = await cls.save_json(path, payload)
        return {
            "path": path,
            "sha256": sha256,
            "byte_size": byte_size,
        }

    @classmethod
    async def load_transcript_version_content(
        cls, path: Optional[str]
    ) -> Optional[Any]:
        payload = await cls.load_json(path)
        if not payload:
            return None
        return payload.get("segments") or []

    @classmethod
    async def save_summary_result(
        cls, meeting_id: str, result: Dict[str, Any], public_key: Optional[str] = None
    ) -> Dict[str, Any]:
        path = cls.summary_path(meeting_id)
        sha256, byte_size, final_path, encryption_wrapper = await cls.save_json(
            path,
            {
                "meeting_id": meeting_id,
                "result": result,
            },
            public_key=public_key
        )
        preview = ""
        if isinstance(result, dict):
            preview = cls._preview_text(
                result.get("markdown")
                or result.get("MeetingName")
                or json.dumps(result, ensure_ascii=False, default=str),
                max_len=500,
            )
        return {
            "path": final_path,
            "sha256": sha256,
            "byte_size": byte_size,
            "preview": preview,
            "encryption": encryption_wrapper,
        }

    @classmethod
    async def load_summary_result(cls, path: Optional[str]) -> Optional[Dict[str, Any]]:
        payload = await cls.load_json(path)
        if not payload:
            return None
        result = payload.get("result")
        return result if isinstance(result, dict) else None
