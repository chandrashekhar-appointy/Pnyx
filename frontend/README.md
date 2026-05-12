# Pnyx - Frontend

A modern web application for recording, transcribing, and analyzing meetings with AI assistance. Built with Next.js.

## Features

- Real-time audio recording from microphone
- Live transcription using Whisper ASR (via Python Backend)
- Speaker diarization support
- Rich text editor for note-taking
- Privacy-focused: All processing happens locally (or on your private server)

## Prerequisites

- Node.js (v18 or later)
- pnpm (v8 or later)

## Project Structure

```
/frontend
├── src/                   # Next.js frontend code
├── public/                # Static assets
└── package.json           # Project dependencies
```

## Installation

1. Install prerequisites:
   - Install [Node.js](https://nodejs.org/) (v18 or later)
   - Install pnpm: `npm install -g pnpm`

2. Clone the repository and navigate to the frontend directory:
   ```bash
   git clone https://github.com/Zackriya-Solutions/meeting-minutes
   cd meeting-minutes/frontend
   ```

3. Install dependencies:
   ```bash
   pnpm install
   ```

## Running the App

### Development

To run the app in development mode:
```bash
pnpm run dev
```
The app will be available at imports `http://localhost:3118` (or similar).

### Production Build

To build and start the production version:
```bash
pnpm run build
pnpm start
```

## Backend Integration

This frontend requires the Python FastAPI backend to be running.
- Backend handles transcription, summarization, and storage.
- Ensure the backend is running on `http://localhost:8000`.

## Development

### Frontend (Next.js)
- The frontend is built with Next.js and Tailwind CSS
- Source code is in the `src/` directory

## Troubleshooting

### Common Issues on macOS
- If you encounter permission issues with scripts, make them executable:
  ```bash
  chmod +x clean_run.sh clean_build.sh whisper-server-package/run-server.sh
  ```
- For microphone access issues, ensure the app has microphone permissions in System Preferences
- If the Whisper server fails to start, check if port 8178 is already in use

### Common Issues on Windows
- If you encounter build errors, ensure Visual Studio Build Tools are properly installed
- For audio capture issues, check Windows privacy settings for microphone access
- If the app fails to start, try running Command Prompt as administrator

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.


