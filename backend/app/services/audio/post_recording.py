"""
Post-Recording Service

Orchestrates post-meeting audio processing:
1. Merge PCM chunks into a single file
2. Convert to WAV format
3. Upload to GCP (if configured)
4. Clean up local PCM chunks
5. Optionally trigger diarization
"""

import asyncio
import logging
import os
import shutil
import json
from pathlib import Path
from typing import Optional, Dict, Tuple, List

try:
    from .recorder import AudioRecorder
    from ..storage import StorageService
    from ...db import DatabaseManager
except (ImportError, ValueError):
    from services.audio.recorder import AudioRecorder
    from services.storage import StorageService
    from db import DatabaseManager

logger = logging.getLogger(__name__)


class PostRecordingService:
    """
    Handles all post-recording processing tasks.

    This service is called after a meeting recording ends to:
    - Finalize and merge audio chunks
    - Upload to cloud storage (GCP)
    - Clean up local temporary files
    - Trigger downstream processing (diarization, summarization)
    """

    def __init__(self, storage_path: str = "./data/recordings"):
        self.storage_path = Path(storage_path)
        self.db = DatabaseManager()
        self.storage_type = os.getenv("STORAGE_TYPE", "local").lower()
        self.recovery_min_duration_ratio = float(
            os.getenv("AUDIO_RECOVERY_MIN_DURATION_RATIO", "0.8")
        )
        self.recovery_min_expected_seconds = float(
            os.getenv("AUDIO_RECOVERY_MIN_EXPECTED_SECONDS", "15")
        )
        self.prefer_compressed_read = (
            os.getenv("AUDIO_PREFER_COMPRESSED_READ", "true").lower() == "true"
        )
        self.skip_wav_finalize_if_compressed = (
            os.getenv("AUDIO_SKIP_WAV_FINALIZE_IF_COMPRESSED", "true").lower() == "true"
        )
        self.delete_local_after_upload = (
            os.getenv("DELETE_LOCAL_AFTER_UPLOAD", "true").lower() == "true"
        )
        self.delete_pcm_after_merge = (
            os.getenv("DELETE_PCM_AFTER_MERGE", "true").lower() == "true"
        )
        self.chunk_prefix = os.getenv("AUDIO_CHUNK_PREFIX", "pcm_chunks")

    async def finalize_recording(
        self,
        meeting_id: str,
        trigger_diarization: bool = False,
        trigger_notes: bool = False,
        user_email: Optional[str] = None,
        transcript_payload: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Complete post-recording processing pipeline.

        Args:
            meeting_id: The meeting ID to process
            trigger_diarization: Whether to auto-trigger diarization
            user_email: User email for API key lookup

        Returns:
            Dict with processing status and file paths
        """
        result = {
            "meeting_id": meeting_id,
            "status": "pending",
            "merged_locally": False,
            "uploaded_to_gcp": False,
            "local_cleaned": False,
            "gcp_path": None,
            "local_path": None,
            "error": None,
        }

        try:
            async with self.db.advisory_lock(
                f"audio-finalize:{meeting_id}"
            ) as acquired:
                if not acquired:
                    result["status"] = "already_running"
                    result["error"] = "Another finalize job is already in progress"
                    logger.info(
                        "⏳ Skipping duplicate finalize for %s because another worker holds the lock",
                        meeting_id,
                    )
                    return result

                logger.info("🔒 Acquired finalize lock for %s", meeting_id)
                recording_dir = self.storage_path / meeting_id

                encrypted_path = await self._get_encrypted_audio_path(meeting_id)
                if encrypted_path:
                    has_chunks = False
                    if self.storage_type == "gcp":
                        prefix = f"{meeting_id}/{self.chunk_prefix}/"
                        files = await StorageService.list_files(prefix)
                        has_chunks = any(f.endswith(".pcm") for f in files)
                    else:
                        local_dir = self.storage_path / meeting_id
                        has_chunks = local_dir.exists() and any(
                            local_dir.glob("chunk_*.pcm")
                        )

                    if not has_chunks:
                        verified = await self._verify_storage_artifact(
                            encrypted_path,
                            min_size_bytes=128,
                        )
                        if verified:
                            result["status"] = "completed"
                            result["uploaded_to_gcp"] = self.storage_type == "gcp"
                            result["gcp_path"] = encrypted_path
                            logger.info(
                                "✅ Post-recording fast path for %s: encrypted artifact already available at %s",
                                meeting_id,
                                encrypted_path,
                            )
                            return result

                if self.prefer_compressed_read and self.skip_wav_finalize_if_compressed:
                    compressed_path = await self._get_compressed_archive_path(
                        meeting_id
                    )
                    if compressed_path:
                        logger.info(f"💾 Found compressed archive: {compressed_path}")

                        has_chunks = False
                        if self.storage_type == "gcp":
                            prefix = f"{meeting_id}/{self.chunk_prefix}/"
                            files = await StorageService.list_files(prefix)
                            has_chunks = any(f.endswith(".pcm") for f in files)
                        else:
                            local_dir = self.storage_path / meeting_id
                            has_chunks = local_dir.exists() and any(
                                local_dir.glob("chunk_*.pcm")
                            )

                        if not has_chunks:
                            verified = await self._verify_storage_artifact(
                                compressed_path,
                                min_size_bytes=128,
                            )
                            if not verified:
                                result["status"] = "verification_failed"
                                result["error"] = (
                                    "Compressed archive exists but failed verification"
                                )
                                return result

                            if (
                                self.storage_type == "gcp"
                                and self.delete_pcm_after_merge
                            ):
                                try:
                                    await self._cleanup_gcp_chunks(meeting_id)
                                    result["local_cleaned"] = True
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to delete PCM chunks in GCS for {meeting_id}: {e}"
                                    )
                            result["status"] = "completed"
                            result["uploaded_to_gcp"] = self.storage_type == "gcp"
                            result["gcp_path"] = compressed_path
                            logger.info(
                                f"✅ Post-recording fast path for {meeting_id}: {compressed_path} already available"
                            )
                            if trigger_diarization:
                                asyncio.create_task(
                                    self._trigger_diarization(meeting_id, user_email)
                                )
                            return result
                        else:
                            logger.info(
                                f"📦 PCM chunks detected for {meeting_id}, ignoring compressed archive to ensure full merge."
                            )

                if self.storage_type == "gcp":
                    logger.info(f"☁️ GCP mode: merging PCM in backend for {meeting_id}")
                    merged = await self._merge_gcp_chunks_to_wav(meeting_id)
                    if not merged:
                        result["status"] = "merge_failed"
                        result["error"] = "Failed to merge PCM chunks in GCP"
                        return result

                    result["uploaded_to_gcp"] = True
                    result["gcp_path"] = f"{meeting_id}/recording.wav"
                    healthy, health = await self._assess_recording_health(
                        meeting_id,
                        artifact_path=result["gcp_path"],
                    )
                    if not healthy:
                        logger.warning(
                            "⚠️ Recording health check failed for %s after merge: %s",
                            meeting_id,
                            health,
                        )
                        recovered = await self._attempt_recovery(
                            meeting_id,
                            artifact_path=result["gcp_path"],
                            health=health,
                        )
                        if not recovered:
                            result["status"] = "recovery_failed"
                            result["error"] = (
                                "Merged WAV failed health check and recovery was unsuccessful"
                            )
                            result["health"] = health
                            return result
                        healthy, health = await self._assess_recording_health(
                            meeting_id,
                            artifact_path=result["gcp_path"],
                        )
                        if not healthy:
                            result["status"] = "recovery_failed"
                            result["error"] = "Recovered WAV still failed health check"
                            result["health"] = health
                            return result

                    result["health"] = health

                    if self.delete_pcm_after_merge:
                        try:
                            await self._cleanup_gcp_chunks(meeting_id)
                            result["local_cleaned"] = True
                        except Exception as e:
                            logger.warning(f"Failed to delete PCM chunks in GCS: {e}")

                    # E2EE Logic: Get user's public key (ONLY IF ENABLED)
                    public_key_spki = None
                    if user_email:
                        is_enabled = await self.db.get_user_encryption_enabled(
                            user_email
                        )
                        if is_enabled:
                            user_info = await self.db.get_user_credits(user_email)
                            if user_info:
                                public_key_spki = user_info.get("encryption_public_key")

                        if public_key_spki:
                            from ..encryption_service import EncryptionService

                            logger.info(
                                f"🔐 E2EE: Encrypting recording and notes for {meeting_id}"
                            )

                            # 1. Encrypt merged audio
                            gcp_wav_path = f"{meeting_id}/recording.wav"
                            audio_data = await StorageService.download_bytes(
                                gcp_wav_path
                            )
                            if not audio_data:
                                logger.error(
                                    f"Failed to download audio for encryption: {gcp_wav_path}"
                                )
                                raise ValueError(f"Missing audio file {gcp_wav_path}")
                            encrypted_audio, audio_wrapper = (
                                EncryptionService.encrypt_document(
                                    audio_data, public_key_spki
                                )
                            )

                            # Save encrypted audio and purge all plaintext audio artifacts.
                            await StorageService.upload_bytes(
                                encrypted_audio, f"{meeting_id}/recording.enc.wav"
                            )
                            await self._delete_plaintext_audio_artifacts(meeting_id)

                            # 2. Process and Encrypt Transcript / Notes
                            encryption_meta = {
                                "audio": audio_wrapper,
                                "transcript": None,
                            }

                            # If transcript_payload not provided (e.g. background task), fetch from DB
                            if not transcript_payload:
                                meeting_data = await self.db.get_meeting(meeting_id)
                                if meeting_data and meeting_data.get("transcripts"):
                                    transcript_payload = meeting_data["transcripts"]
                                    logger.info(
                                        f"📑 E2EE: Fetched {len(transcript_payload)} segments from DB for encryption"
                                    )

                            if transcript_payload:
                                transcript_json = json.dumps(transcript_payload).encode(
                                    "utf-8"
                                )
                                enc_transcript, trans_wrapper = (
                                    EncryptionService.encrypt_document(
                                        transcript_json, public_key_spki
                                    )
                                )

                                await StorageService.upload_bytes(
                                    enc_transcript, f"{meeting_id}/transcript.enc.json"
                                )
                                encryption_meta["transcript"] = trans_wrapper

                            # Update database with encryption metadata (REQUIRED for audio download even if transcript is empty)
                            async with self.db._get_connection() as conn:
                                existing_process = await self.db.get_transcript_data(
                                    meeting_id
                                )
                                if existing_process:
                                    # Ensure we have a dict, parsing string if necessary
                                    current_metadata = (
                                        existing_process.get("metadata") or {}
                                    )
                                    if isinstance(current_metadata, str):
                                        try:
                                            current_metadata = json.loads(
                                                current_metadata
                                            )
                                        except Exception:
                                            current_metadata = {}

                                    current_metadata["encryption"] = encryption_meta

                                    # Serialize back to JSON string since asyncpg expects it for this column
                                    await conn.execute(
                                        "UPDATE summary_processes SET metadata = $1, status = 'completed' WHERE meeting_id = $2",
                                        json.dumps(current_metadata),
                                        meeting_id,
                                    )
                                else:
                                    # Creating fresh metadata for a new process entry
                                    initial_metadata = {"encryption": encryption_meta}
                                    await conn.execute(
                                        """
                                        INSERT INTO summary_processes 
                                        (meeting_id, status, metadata, start_time, end_time)
                                        VALUES ($1, 'completed', $2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                                        """,
                                        meeting_id,
                                        json.dumps(initial_metadata),
                                    )

                            if transcript_payload:
                                # PURGE PLAINTEXT: Now that it's safely encrypted in storage, remove from DB
                                await self.db.clear_meeting_transcripts(meeting_id)
                                logger.info(
                                    f"🧹 E2EE: Purged plaintext transcripts for {meeting_id}"
                                )

                        logger.info(f"✅ E2EE: Encryption complete for {meeting_id}")

                    result["status"] = "completed"
                    logger.info(f"✅ Post-recording (GCP) complete for {meeting_id}")

                    if trigger_diarization and not public_key_spki:
                        # Diarization needs raw audio, so we skip if encrypted for now
                        asyncio.create_task(
                            self._trigger_diarization(meeting_id, user_email)
                        )

                    if trigger_notes:
                        asyncio.create_task(
                            self._trigger_notes_generation(meeting_id, user_email)
                        )

                    return result

                if not recording_dir.exists():
                    result["status"] = "no_recording"
                    result["error"] = f"No recording found for meeting {meeting_id}"
                    logger.warning(f"No recording directory found: {recording_dir}")
                    return result

                logger.info(f"📼 Step 1: Merging PCM chunks for meeting {meeting_id}")
                merged_pcm = await self._merge_chunks(meeting_id)

                if not merged_pcm:
                    logger.warning(
                        f"Merge returned None, attempting manual chunk scan for {meeting_id}"
                    )
                    chunk_dir = self.storage_path / meeting_id
                    if chunk_dir.exists() and list(chunk_dir.glob("chunk_*.pcm")):
                        logger.info("Found orphan chunks, retrying merge...")
                        merged_pcm = await AudioRecorder.merge_chunks(
                            meeting_id, str(self.storage_path)
                        )

                if not merged_pcm:
                    wav_path = self.storage_path / meeting_id / "recording.wav"
                    if wav_path.exists():
                        logger.info("Found existing recording.wav, using that.")
                        result["merged_locally"] = True
                        result["local_path"] = str(wav_path)
                    else:
                        result["status"] = "merge_failed"
                        result["error"] = (
                            "Failed to merge audio chunks and no existing WAV found"
                        )
                        return result

                if merged_pcm:
                    logger.info("🎵 Step 2: Converting to WAV format")
                    wav_path = await self._convert_to_wav(meeting_id, merged_pcm)

                    if not wav_path:
                        result["status"] = "conversion_failed"
                        result["error"] = "Failed to convert to WAV"
                        return result

                    result["merged_locally"] = True
                    result["local_path"] = str(wav_path)

                if not result.get("local_path"):
                    result["status"] = "error"
                    result["error"] = "Lost audio file path reference"
                    return result

                wav_path = Path(result["local_path"])
                healthy, health = await self._assess_recording_health(
                    meeting_id,
                    artifact_path=str(wav_path),
                    local_override_path=wav_path,
                )
                if not healthy:
                    recovered = await self._attempt_recovery(
                        meeting_id,
                        artifact_path=str(wav_path),
                        health=health,
                        local_override_path=wav_path,
                    )
                    if not recovered:
                        result["status"] = "recovery_failed"
                        result["error"] = (
                            "Local WAV failed health check and recovery was unsuccessful"
                        )
                        result["health"] = health
                        return result
                    healthy, health = await self._assess_recording_health(
                        meeting_id,
                        artifact_path=str(wav_path),
                        local_override_path=wav_path,
                    )
                    if not healthy:
                        result["status"] = "recovery_failed"
                        result["error"] = (
                            "Recovered local WAV still failed health check"
                        )
                        result["health"] = health
                        return result
                result["health"] = health

                if self.storage_type == "gcp":
                    logger.info("☁️ Step 3: Uploading to GCP")
                    gcp_path = await self._upload_to_gcp(meeting_id, wav_path)

                    if gcp_path:
                        result["uploaded_to_gcp"] = True
                        result["gcp_path"] = gcp_path
                        (
                            healthy_remote,
                            remote_health,
                        ) = await self._assess_recording_health(
                            meeting_id,
                            artifact_path=gcp_path,
                        )
                        if not healthy_remote:
                            recovered = await self._attempt_recovery(
                                meeting_id,
                                artifact_path=gcp_path,
                                health=remote_health,
                            )
                            if not recovered:
                                logger.warning(
                                    "GCP upload failed recovery health check, keeping local files"
                                )
                                result["status"] = "recovery_failed"
                                result["error"] = (
                                    "Uploaded artifact failed health check and recovery was unsuccessful"
                                )
                                result["health"] = remote_health
                                return result
                            (
                                healthy_remote,
                                remote_health,
                            ) = await self._assess_recording_health(
                                meeting_id,
                                artifact_path=gcp_path,
                            )
                            if not healthy_remote:
                                result["status"] = "recovery_failed"
                                result["error"] = (
                                    "Recovered uploaded artifact still failed health check"
                                )
                                result["health"] = remote_health
                                return result

                        result["health"] = remote_health
                        if self.delete_local_after_upload:
                            logger.info("🗑️ Step 4: Cleaning up local files")
                            await self._cleanup_local(meeting_id, keep_wav=False)
                            result["local_cleaned"] = True
                    else:
                        logger.warning(
                            "GCP upload failed verification, keeping local files"
                        )
                        result["status"] = "verification_failed"
                        result["error"] = "Uploaded artifact could not be verified"
                        return result
                else:
                    logger.info("📁 Step 3: Local storage mode - skipping GCP upload")

                result["status"] = "completed"
                logger.info(f"✅ Post-recording processing complete for {meeting_id}")

                if trigger_diarization:
                    asyncio.create_task(
                        self._trigger_diarization(meeting_id, user_email)
                    )

                if trigger_notes:
                    asyncio.create_task(
                        self._trigger_notes_generation(meeting_id, user_email)
                    )

                return result

        except Exception as e:
            logger.error(f"Post-recording processing failed: {e}", exc_info=True)
            result["status"] = "error"
            result["error"] = str(e)
            return result

    async def _verify_storage_artifact(
        self, path: str, min_size_bytes: int = 1
    ) -> bool:
        exists = await StorageService.check_file_exists(path)
        if not exists:
            logger.warning("Artifact verification failed; file missing: %s", path)
            return False

        size = await StorageService.get_file_size(path)
        if size is None:
            logger.warning(
                "Artifact verification could not determine size for %s", path
            )
            return False
        if size < min_size_bytes:
            logger.warning(
                "Artifact verification failed; %s is too small (%s bytes, expected >= %s)",
                path,
                size,
                min_size_bytes,
            )
            return False
        return True

    async def _verify_local_path(self, path: Path, min_size_bytes: int = 1) -> bool:
        try:
            if not path.exists():
                logger.warning("Local artifact verification failed; missing: %s", path)
                return False
            size = path.stat().st_size
            if size < min_size_bytes:
                logger.warning(
                    "Local artifact verification failed; %s is too small (%s bytes, expected >= %s)",
                    path,
                    size,
                    min_size_bytes,
                )
                return False
            return True
        except Exception as e:
            logger.warning("Local artifact verification errored for %s: %s", path, e)
            return False

    async def _assess_recording_health(
        self,
        meeting_id: str,
        artifact_path: str,
        local_override_path: Optional[Path] = None,
    ) -> Tuple[bool, Dict]:
        if local_override_path:
            verified = await self._verify_local_path(
                local_override_path, min_size_bytes=45
            )
            size_bytes = (
                int(local_override_path.stat().st_size)
                if local_override_path.exists()
                else None
            )
        else:
            verified = await self._verify_storage_artifact(
                artifact_path, min_size_bytes=45
            )
            size_bytes = await StorageService.get_file_size(artifact_path)

        expected_duration = await self._get_expected_pcm_duration_seconds(meeting_id)
        actual_duration = (
            self._wav_duration_seconds_from_size(size_bytes)
            if size_bytes is not None
            else None
        )
        duration_ratio = None
        suspiciously_short = False
        if expected_duration and actual_duration is not None and expected_duration > 0:
            duration_ratio = actual_duration / expected_duration
            suspiciously_short = (
                expected_duration >= self.recovery_min_expected_seconds
                and duration_ratio < self.recovery_min_duration_ratio
            )

        health = {
            "artifact_path": artifact_path,
            "verified": verified,
            "size_bytes": size_bytes,
            "expected_duration_seconds": expected_duration,
            "actual_duration_seconds": actual_duration,
            "duration_ratio": duration_ratio,
            "suspiciously_short": suspiciously_short,
        }
        return bool(verified and not suspiciously_short), health

    async def _attempt_recovery(
        self,
        meeting_id: str,
        artifact_path: str,
        health: Dict,
        local_override_path: Optional[Path] = None,
    ) -> bool:
        if not health.get("expected_duration_seconds"):
            logger.warning(
                "Skipping recovery for %s because expected duration is unavailable",
                meeting_id,
            )
            return False

        logger.warning(
            "🛠️ Attempting recording recovery for %s (artifact=%s health=%s)",
            meeting_id,
            artifact_path,
            health,
        )
        if self.storage_type == "gcp" and not local_override_path:
            return await self._merge_gcp_chunks_to_wav(
                meeting_id, append_existing=False
            )

        merged_pcm = await self._merge_chunks(meeting_id)
        if not merged_pcm:
            return False
        wav_path = await self._convert_to_wav(
            meeting_id,
            merged_pcm,
            append_existing=False,
        )
        return bool(
            wav_path and await self._verify_local_path(wav_path, min_size_bytes=45)
        )

    async def _get_expected_pcm_duration_seconds(
        self, meeting_id: str
    ) -> Optional[float]:
        metadata = await self._load_chunk_metadata(meeting_id)
        chunks = metadata.get("chunks") if isinstance(metadata, dict) else None
        if chunks:
            durations = [
                float(chunk.get("duration_seconds") or 0.0)
                for chunk in chunks
                if isinstance(chunk, dict)
            ]
            total = sum(d for d in durations if d > 0)
            if total > 0:
                return total

        if self.storage_type == "gcp":
            prefix = f"{meeting_id}/{self.chunk_prefix}/"
            files = await StorageService.list_files(prefix)
            chunk_files = [f for f in files if f.endswith(".pcm")]
            if not chunk_files:
                return None
            total_bytes = 0
            for path in chunk_files:
                size = await StorageService.get_file_size(path)
                if size:
                    total_bytes += int(size)
            return total_bytes / float(16000 * 2) if total_bytes > 0 else None

        chunk_dir = self.storage_path / meeting_id
        if not chunk_dir.exists():
            return None
        total_bytes = sum(path.stat().st_size for path in chunk_dir.glob("chunk_*.pcm"))
        return total_bytes / float(16000 * 2) if total_bytes > 0 else None

    async def _load_chunk_metadata(self, meeting_id: str) -> Dict:
        try:
            if self.storage_type == "gcp":
                metadata_path = f"{meeting_id}/{self.chunk_prefix}/metadata.json"
                metadata_bytes = await StorageService.download_bytes(metadata_path)
                if not metadata_bytes:
                    return {}
                return json.loads(metadata_bytes.decode("utf-8"))

            metadata_path = self.storage_path / meeting_id / "metadata.json"
            if not metadata_path.exists():
                return {}
            return json.loads(metadata_path.read_text())
        except Exception:
            return {}

    @staticmethod
    def _wav_duration_seconds_from_size(size_bytes: Optional[int]) -> Optional[float]:
        if size_bytes is None:
            return None
        if size_bytes <= 44:
            return 0.0
        return max(0.0, float(size_bytes - 44) / float(16000 * 2))

    async def _get_compressed_archive_path(self, meeting_id: str) -> Optional[str]:
        """
        Return compressed archive path if present.
        """
        candidates = [f"{meeting_id}/recording.opus", f"{meeting_id}/recording.m4a"]
        try:
            # If PCM chunks exist, force full merge path to ensure resumed sessions
            # are represented in the final recording artifact.
            if self.storage_type == "gcp":
                prefix = f"{meeting_id}/{self.chunk_prefix}/"
                files = await StorageService.list_files(prefix)
                if any(f.endswith(".pcm") for f in files):
                    return None
            else:
                local_dir = self.storage_path / meeting_id
                if local_dir.exists() and any(local_dir.glob("chunk_*.pcm")):
                    return None

            if self.storage_type == "gcp":
                for path in candidates:
                    if await StorageService.check_file_exists(path):
                        return path
                return None

            local_dir = self.storage_path / meeting_id
            for name, path in (
                ("recording.opus", f"{meeting_id}/recording.opus"),
                ("recording.m4a", f"{meeting_id}/recording.m4a"),
            ):
                if (local_dir / name).exists():
                    return path
            return None
        except Exception:
            return None

    async def _get_encrypted_audio_path(self, meeting_id: str) -> Optional[str]:
        encrypted_path = f"{meeting_id}/recording.enc.wav"
        try:
            if self.storage_type == "gcp":
                return (
                    encrypted_path
                    if await StorageService.check_file_exists(encrypted_path)
                    else None
                )

            local_path = self.storage_path / meeting_id / "recording.enc.wav"
            return encrypted_path if local_path.exists() else None
        except Exception:
            return None

    async def _delete_plaintext_audio_artifacts(self, meeting_id: str) -> None:
        plaintext_paths = [
            f"{meeting_id}/recording.wav",
            f"{meeting_id}/recording.opus",
            f"{meeting_id}/recording.m4a",
        ]
        for path in plaintext_paths:
            try:
                await StorageService.delete_file(path)
            except Exception as exc:
                logger.warning(
                    "Failed deleting plaintext audio artifact for %s at %s: %s",
                    meeting_id,
                    path,
                    exc,
                )

    async def _merge_gcp_chunks_to_wav(
        self, meeting_id: str, append_existing: bool = True
    ) -> bool:
        """
        Merge PCM chunks stored in GCS into a WAV file, upload to GCS.
        No local disk usage; uses in-memory buffering.
        """
        try:
            try:
                from ..storage import StorageService
            except (ImportError, ValueError):
                from services.storage import StorageService

            prefix = f"{meeting_id}/{self.chunk_prefix}/"
            files = await StorageService.list_files(prefix)
            chunk_files = sorted([f for f in files if f.endswith(".pcm")])

            if not chunk_files:
                logger.error(f"No PCM chunks found in GCS for {meeting_id}")
                return False

            logger.info(
                f"📂 Merging {len(chunk_files)} PCM chunks from GCP for {meeting_id}"
            )
            for i, f in enumerate(chunk_files[:5]):
                logger.debug(f"  [{i}] {f}")
            if len(chunk_files) > 5:
                logger.debug(f"  ... and {len(chunk_files) - 5} more")

            import io
            import wave

            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)

                # Check for existing recording.wav to append to
                if append_existing:
                    existing_wav_path = f"{meeting_id}/recording.wav"
                    existing_wav_bytes = await StorageService.download_bytes(
                        existing_wav_path
                    )
                    if existing_wav_bytes and len(existing_wav_bytes) > 44:
                        logger.info(
                            f"Found existing GCP recording.wav for {meeting_id}, appending new PCM chunks."
                        )
                        wav_file.writeframes(existing_wav_bytes[44:])

                for blob_name in chunk_files:
                    chunk = await StorageService.download_bytes(blob_name)
                    if chunk:
                        wav_file.writeframes(chunk)

            wav_buffer.seek(0)
            wav_bytes = wav_buffer.read()

            uploaded = await StorageService.upload_bytes(
                wav_bytes, f"{meeting_id}/recording.wav", content_type="audio/wav"
            )
            if not uploaded:
                logger.error("Failed to upload merged WAV to GCS")
                return False

            logger.info(
                f"✅ Uploaded merged WAV for {meeting_id} ({len(wav_bytes) / 1024 / 1024:.2f} MB)"
            )
            return True
        except Exception as e:
            logger.error(f"Merge PCM in backend failed: {e}", exc_info=True)
            return False

    async def _cleanup_gcp_chunks(self, meeting_id: str) -> bool:
        try:
            try:
                from ..storage import StorageService
            except (ImportError, ValueError):
                from services.storage import StorageService

            prefix = f"{meeting_id}/{self.chunk_prefix}/"
            deleted = await StorageService.delete_prefix(prefix)
            remaining = await StorageService.list_files(prefix)
            if remaining:
                logger.warning(
                    "GCS chunk cleanup left %s objects for %s under %s; retrying once",
                    len(remaining),
                    meeting_id,
                    prefix,
                )
                deleted = await StorageService.delete_prefix(prefix) and deleted
                remaining = await StorageService.list_files(prefix)

            if remaining:
                logger.warning(
                    "GCS chunk cleanup incomplete for %s; remaining objects: %s",
                    meeting_id,
                    remaining[:10],
                )
                return False
            return deleted
        except Exception as e:
            logger.error(f"GCS cleanup failed: {e}")
            return False

    async def _merge_chunks(self, meeting_id: str) -> Optional[bytes]:
        """Merge all PCM chunks for a meeting."""
        try:
            pcm_data = await AudioRecorder.merge_chunks(
                meeting_id, str(self.storage_path)
            )
            return pcm_data
        except Exception as e:
            logger.error(f"Failed to merge chunks: {e}")
            return None

    async def _convert_to_wav(
        self, meeting_id: str, pcm_data: bytes, append_existing: bool = True
    ) -> Optional[Path]:
        """Convert PCM to WAV and append to existing WAV locally if present."""
        try:
            wav_path = self.storage_path / meeting_id / "recording.wav"
            import aiofiles

            existing_pcm = b""
            if append_existing and wav_path.exists():
                logger.info(
                    f"Found existing recording.wav for {meeting_id}, appending new PCM chunks."
                )
                async with aiofiles.open(wav_path, "rb") as f:
                    old_wav = await f.read()
                    if len(old_wav) > 44:
                        existing_pcm = old_wav[44:]

            total_pcm = existing_pcm + pcm_data
            wav_data = AudioRecorder.convert_pcm_to_wav(total_pcm)

            async with aiofiles.open(wav_path, "wb") as f:
                await f.write(wav_data)

            logger.info(
                f"WAV file saved: {wav_path} ({len(wav_data) / 1024 / 1024:.2f} MB)"
            )
            return wav_path

        except Exception as e:
            logger.error(f"Failed to convert to WAV: {e}")
            return None

    async def _upload_to_gcp(
        self, meeting_id: str, local_wav_path: Path
    ) -> Optional[str]:
        """Upload WAV file to GCP bucket."""
        try:
            gcp_path = f"{meeting_id}/recording.wav"

            success = await StorageService.upload_file(str(local_wav_path), gcp_path)

            if success:
                logger.info(f"✅ Uploaded to GCP: {gcp_path}")
                return gcp_path
            else:
                logger.error("GCP upload returned False")
                return None

        except Exception as e:
            logger.error(f"GCP upload failed: {e}")
            return None

    async def _cleanup_local(self, meeting_id: str, keep_wav: bool = True) -> bool:
        """
        Clean up local PCM chunks after successful GCP upload.

        Args:
            meeting_id: Meeting ID
            keep_wav: If True, keep the merged WAV file locally
        """
        try:
            recording_dir = self.storage_path / meeting_id

            if not recording_dir.exists():
                return True

            # Delete PCM chunks
            for pcm_file in recording_dir.glob("chunk_*.pcm"):
                pcm_file.unlink()
                logger.debug(f"Deleted: {pcm_file}")

            chunk_subdir = recording_dir / self.chunk_prefix
            if chunk_subdir.exists():
                shutil.rmtree(chunk_subdir, ignore_errors=True)

            # Delete merged PCM if it exists
            merged_pcm = recording_dir / "merged_recording.pcm"
            if merged_pcm.exists():
                merged_pcm.unlink()

            # Optionally delete WAV
            if not keep_wav:
                wav_file = recording_dir / "recording.wav"
                if wav_file.exists():
                    wav_file.unlink()
                    logger.debug(f"Deleted WAV: {wav_file}")

                # Also try to delete merged_recording.wav
                merged_wav = recording_dir / "merged_recording.wav"
                if merged_wav.exists():
                    merged_wav.unlink()

                for archive_name in (
                    "recording.opus",
                    "recording.m4a",
                    "recording.enc.wav",
                    "metadata.json",
                ):
                    archive_path = recording_dir / archive_name
                    if archive_path.exists():
                        archive_path.unlink()

            # Clean up empty directory
            remaining_files = list(recording_dir.iterdir())
            if not remaining_files:
                recording_dir.rmdir()
                logger.info(f"Removed empty recording directory: {recording_dir}")

            logger.info(f"Local cleanup complete for {meeting_id}")
            return True

        except Exception as e:
            logger.error(f"Local cleanup failed: {e}")
            return False

    async def _trigger_diarization(
        self, meeting_id: str, user_email: Optional[str] = None
    ):
        """Trigger background diarization job."""
        try:
            # Import here to avoid circular imports
            from ..audio.diarization import get_diarization_service

            get_diarization_service()
            logger.info(f"🎯 Auto-triggering diarization for {meeting_id}")

            # This would need proper integration with the diarization job system
            # For now, just log the intent
            # await service.diarize_meeting(meeting_id)

        except Exception as e:
            logger.error(f"Failed to trigger diarization: {e}")

    async def _trigger_notes_generation(
        self, meeting_id: str, user_email: Optional[str] = None
    ):
        """Trigger background notes generation job."""
        try:
            logger.info(f"📝 Auto-triggering notes generation for {meeting_id}")
            try:
                from ...tasks.generate_notes import generate_meeting_notes_task
            except (ImportError, ValueError):
                from tasks.generate_notes import generate_meeting_notes_task
                
            generate_meeting_notes_task.delay(
                meeting_id=meeting_id,
                user_email=user_email or "default",
                source="live_meeting",
            )
            logger.info(f"✅ Notes generation task queued for {meeting_id}")
        except Exception as e:
            logger.error(f"Failed to trigger notes generation: {e}")


# Singleton instance
_post_recording_service: Optional[PostRecordingService] = None


def get_post_recording_service() -> PostRecordingService:
    """Get or create the post-recording service singleton."""
    global _post_recording_service

    if _post_recording_service is None:
        storage_path = os.getenv("RECORDINGS_STORAGE_PATH", "./data/recordings")
        _post_recording_service = PostRecordingService(storage_path)

    return _post_recording_service
