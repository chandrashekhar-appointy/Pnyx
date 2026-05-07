import os
import sys
import asyncio
import logging
from pathlib import Path

# Force GCP settings BEFORE importing app modules
# This ensures storage.py picks up the correct config
os.environ["STORAGE_TYPE"] = "gcp"
os.environ["GCP_BUCKET_NAME"] = "pnyx-recordings"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/app/gcp-service-account.json"

# In container, we are in /app, so imports should work directly
try:
    from storage import StorageService
    from audio_recorder import AudioRecorder
    import aiofiles
except ImportError as e:
    print(f"Error importing modules: {e}")
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration")


async def migrate_meeting(meeting_id: str, local_path: Path):
    """Migrate a single meeting to GCS."""
    logger.info(f"Processing meeting: {meeting_id}")

    merged_wav = local_path / "merged_recording.wav"
    merged_pcm = local_path / "merged_recording.pcm"

    source_file = None
    if merged_wav.exists():
        source_file = merged_wav
    elif merged_pcm.exists():
        logger.info(f"  Converting PCM to WAV for {meeting_id}")
        async with aiofiles.open(merged_pcm, "rb") as f:
            pcm_data = await f.read()
        wav_data = AudioRecorder.convert_pcm_to_wav(pcm_data)
        async with aiofiles.open(merged_wav, "wb") as f:
            await f.write(wav_data)
        source_file = merged_wav
    else:
        chunks = list(local_path.glob("chunk_*.pcm"))
        if chunks:
            logger.info(f"  Merging {len(chunks)} chunks for {meeting_id}")
            audio_bytes = await AudioRecorder.merge_chunks(
                meeting_id, str(local_path.parent)
            )
            if audio_bytes:
                wav_data = AudioRecorder.convert_pcm_to_wav(audio_bytes)
                async with aiofiles.open(merged_wav, "wb") as f:
                    await f.write(wav_data)
                source_file = merged_wav
        else:
            logger.warning(f"  No audio found for {meeting_id}, skipping")
            return

    if source_file and source_file.exists():
        destination = f"{meeting_id}/recording.wav"
        logger.info(
            f"  Uploading {source_file} -> gs://{os.environ['GCP_BUCKET_NAME']}/{destination}"
        )

        success = await StorageService.upload_file(str(source_file), destination)

        if success:
            logger.info(f"✅ Successfully migrated {meeting_id}")
        else:
            logger.error(f"❌ Failed to upload {meeting_id}")


async def main():
    # Hardcode path for container
    recordings_dir = Path("/app/data/recordings")
    if not recordings_dir.exists():
        logger.error(f"Recordings directory not found: {recordings_dir}")
        return

    logger.info(f"Starting migration to bucket: {os.environ['GCP_BUCKET_NAME']}")

    tasks = []
    for meeting_dir in recordings_dir.iterdir():
        if meeting_dir.is_dir():
            tasks.append(migrate_meeting(meeting_dir.name, meeting_dir))

    if not tasks:
        logger.info("No meetings found to migrate.")
        return

    batch_size = 5
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i : i + batch_size]
        await asyncio.gather(*batch)
        logger.info(
            f"Processed batch {i // batch_size + 1}/{(len(tasks) + batch_size - 1) // batch_size}"
        )

    logger.info("🎉 Migration complete!")


if __name__ == "__main__":
    asyncio.run(main())
