# Gemini Voice Bridge

Real-time voice bridge connecting Twilio Media Streams to Google Gemini Live API.

## Overview

This service enables real-time voice conversations with Google's Gemini AI through phone calls. When a call comes in via Twilio, the audio is streamed to Gemini Live API and responses are streamed back to the caller.

## Architecture

```
Phone Call → Twilio → Media Stream WebSocket → This Service → Gemini Live API
                                    ↓
                              Voice Response
```

## Endpoints

- `GET /health` - Health check for Railway
- `POST /voice/webhook` - Twilio voice webhook, returns TwiML to connect media stream
- `WS /media-stream` - WebSocket endpoint for Twilio Media Streams

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Google AI API key for Gemini |
| `TWILIO_AUTH_TOKEN` | Twilio auth token (for request validation) |

## Deployment

### Railway

1. Connect this repo to Railway
2. Set environment variables
3. Deploy

### Local Development

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=your_key
uvicorn main:app --reload
```

## Twilio Configuration

Point your Twilio phone number's Voice webhook to:
```
https://your-domain.railway.app/voice/webhook
```

## Tech Stack

- **FastAPI** - Web framework
- **WebSockets** - Real-time audio streaming
- **Gemini Live API** - Google's real-time voice AI

## License

Private - HalloPetra GmbH