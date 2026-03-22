<div align="center" style="border-bottom: none">
    <h1>
        Meeting Co-Pilot
        <br>
        AI-Powered Collaborative Meeting Assistant
    </h1>
    <br>
    <img src="https://img.shields.io/badge/License-MIT-blue" alt="License">
    <img src="https://img.shields.io/badge/Platform-Web-brightgreen" alt="Platform">
    <br>
    <h3>
    <br>
    Open Source • Real-Time Transcription • AI-Powered
    </h3>
    <p align="center">

 A web-based collaborative meeting assistant with real-time transcription using Groq Whisper API. Designed for on-site meetings with live transcript sharing, AI-powered catch-up features, and cross-meeting context.
</p>

</div>

<details>
<summary>Table of Contents</summary>

- [Introduction](#introduction)
- [Features](#features)
- [Installation](#installation)
- [System Architecture](#system-architecture)
- [For Developers](#for-developers)
- [Contributing](#contributing)
- [License](#license)

</details>

## Introduction

Meeting Co-Pilot is a web-based collaborative meeting assistant forked from Meetily. It provides real-time transcription using Groq Whisper API with support for Hindi/English (Hinglish) mixed-language meetings.

**Key Differences from Meetily:**
- **Web-based**: Pure Next.js + FastAPI (no Tauri desktop app)
- **Real-time streaming**: Continuous PCM audio streaming with ~1-2s latency
- **Groq Whisper API**: Cloud-based transcription for faster processing
- **Collaborative**: Designed for multi-participant on-site meetings

## Features

- **Real-time Transcription:** Get a live transcript of your meeting as it happens (~1-2s latency).
- **Hinglish Support:** Auto language detection for Hindi/English code-switching.
- **AI-Powered Summaries:** Generate summaries of your meetings using Claude, Groq, or Ollama.
- **Web-Based:** Works on any modern browser (Chrome, Firefox, Safari, Edge).
- **Open Source:** Meeting Co-Pilot is open source and free to use.

## System Architecture

Meeting Co-Pilot is a web-based application.
- **Frontend**: Next.js (React) application with AudioWorklet for real-time audio capture.
- **Backend**: Python FastAPI server with WebSocket streaming, VAD, and Groq Whisper API.
- **Database**: PostgreSQL for meeting storage.

## Installation

### Running with Docker (Recommended)

1. Ensure Docker is installed.
2. Run the start script:
   ```bash
   ./run-docker.sh
   ```
   This will start both the frontend and backend services.
   Access the app at `http://localhost:3118`.

### Manual Installation

#### Backend
1. Navigate to `/backend`.
2. Install Python dependencies: `pip install -r requirements.txt`.
3. Run the server: `python app/main.py`.

#### Frontend
1. Navigate to `/frontend`.
2. Install dependencies: `pnpm install`.
3. Run the dev server: `pnpm run dev`.

For more details, see the [Architecture documentation](docs/architecture.md).

## For Developers

If you want to contribute to Meeting Co-Pilot or build it from source, you'll need Node.js and Python installed. For detailed build instructions, please see the [Building from Source guide](docs/BUILDING.md).

## Contributing

We welcome contributions from the community! If you have any questions or suggestions, please open an issue or submit a pull request. Please follow the established project structure and guidelines. For more details, refer to the [CONTRIBUTING.md](CONTRIBUTING.md) file.

## License

MIT License - Feel free to use this project for your own purposes.

## Acknowledgments

- Forked from [Meetily](https://github.com/Zackriya-Solutions/meeting-minutes) by Zackriya Solutions.
- Uses [Groq Whisper API](https://groq.com/) for real-time transcription.
- Uses [Whisper.cpp](https://github.com/ggerganov/whisper.cpp) for local transcription option.

