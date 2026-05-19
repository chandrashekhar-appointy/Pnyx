import asyncio
import os
from app.services.audio.elevenlabs_client import ElevenLabsTranscriptionClient

async def test():
    sample_rate = 16000
    audio_data = b'\x00\x00' * sample_rate
    
    os.environ["ELEVENLABS_API_KEY"] = "sk_dd72ec33d95460e807d5b90e05bd4d6b99cebe234133c2e6"
    client = ElevenLabsTranscriptionClient(mode="batch")
    
    result = await client.transcribe_audio_async(audio_data)
    print("RESULT:", result)

if __name__ == "__main__":
    asyncio.run(test())
