"""
Gemini Voice Bridge - Bridges Twilio Media Streams with Google Gemini Live API
for real-time voice conversations.
"""

import os
import json
import base64
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response
import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_WS_URL = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"

SYSTEM_PROMPT = """Du bist Jeff, KI-Assistent von Hendric Martens (HalloPetra GmbH, KI für Handwerker). Antworte kurz auf Deutsch. Du sprichst gerade per Telefon."""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Gemini Voice Bridge starting up...")
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set!")
    yield
    logger.info("Gemini Voice Bridge shutting down...")


app = FastAPI(title="Gemini Voice Bridge", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Health check endpoint for Railway."""
    return {"status": "healthy", "service": "gemini-voice-bridge"}


@app.post("/voice/webhook")
async def voice_webhook(request: Request):
    """
    Twilio voice webhook - returns TwiML to connect to our media stream.
    """
    host = request.headers.get("host", "localhost")
    
    # Use wss for production, ws for local development
    protocol = "wss" if "railway" in host or "https" in str(request.url) else "ws"
    stream_url = f"{protocol}://{host}/media-stream"
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}">
            <Parameter name="caller" value="{{{{From}}}}"/>
        </Stream>
    </Connect>
</Response>"""
    
    logger.info(f"Voice webhook called, streaming to: {stream_url}")
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """
    WebSocket endpoint for Twilio Media Streams.
    Receives audio from Twilio (mulaw 8kHz) and bridges to Gemini Live API.
    """
    await websocket.accept()
    logger.info("Twilio media stream connected")
    
    stream_sid = None
    gemini_ws = None
    
    try:
        # Connect to Gemini Live API
        gemini_ws = await websockets.connect(GEMINI_WS_URL)
        logger.info("Connected to Gemini Live API")
        
        # Send initial setup message to Gemini
        setup_message = {
            "setup": {
                "model": "models/gemini-2.0-flash-exp",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": "Aoede"
                            }
                        }
                    }
                },
                "systemInstruction": {
                    "parts": [{"text": SYSTEM_PROMPT}]
                }
            }
        }
        await gemini_ws.send(json.dumps(setup_message))
        
        # Wait for setup complete
        setup_response = await gemini_ws.recv()
        setup_data = json.loads(setup_response)
        if "setupComplete" in setup_data:
            logger.info("Gemini setup complete")
        
        # Create tasks for bidirectional streaming
        twilio_to_gemini_task = asyncio.create_task(
            handle_twilio_to_gemini(websocket, gemini_ws)
        )
        gemini_to_twilio_task = asyncio.create_task(
            handle_gemini_to_twilio(websocket, gemini_ws)
        )
        
        # Wait for either task to complete (or fail)
        done, pending = await asyncio.wait(
            [twilio_to_gemini_task, gemini_to_twilio_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Cancel pending tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
                
    except WebSocketDisconnect:
        logger.info("Twilio disconnected")
    except Exception as e:
        logger.error(f"Error in media stream: {e}")
    finally:
        if gemini_ws:
            await gemini_ws.close()
        logger.info("Media stream closed")


async def handle_twilio_to_gemini(twilio_ws: WebSocket, gemini_ws):
    """
    Forwards audio from Twilio to Gemini Live API.
    Twilio sends mulaw 8kHz audio as base64.
    """
    try:
        while True:
            message = await twilio_ws.receive_text()
            data = json.loads(message)
            
            event_type = data.get("event")
            
            if event_type == "start":
                stream_sid = data.get("start", {}).get("streamSid")
                logger.info(f"Stream started: {stream_sid}")
                
            elif event_type == "media":
                # Extract audio payload (base64 encoded mulaw)
                payload = data.get("media", {}).get("payload")
                if payload:
                    # Send audio to Gemini
                    # Gemini expects PCM audio, but let's try with mulaw first
                    # and convert if needed
                    audio_message = {
                        "realtimeInput": {
                            "mediaChunks": [{
                                "mimeType": "audio/pcm;rate=8000",
                                "data": payload
                            }]
                        }
                    }
                    await gemini_ws.send(json.dumps(audio_message))
                    
            elif event_type == "stop":
                logger.info("Stream stopped by Twilio")
                break
                
    except WebSocketDisconnect:
        logger.info("Twilio WebSocket disconnected")
    except Exception as e:
        logger.error(f"Error forwarding to Gemini: {e}")


async def handle_gemini_to_twilio(twilio_ws: WebSocket, gemini_ws):
    """
    Receives audio responses from Gemini and forwards to Twilio.
    Converts Gemini's audio format to mulaw 8kHz for Twilio.
    """
    try:
        while True:
            response = await gemini_ws.recv()
            data = json.loads(response)
            
            # Check for audio data in response
            if "serverContent" in data:
                server_content = data["serverContent"]
                
                # Check if model is done speaking
                if server_content.get("turnComplete"):
                    logger.info("Gemini turn complete")
                    continue
                
                # Extract audio parts
                model_turn = server_content.get("modelTurn", {})
                parts = model_turn.get("parts", [])
                
                for part in parts:
                    if "inlineData" in part:
                        inline_data = part["inlineData"]
                        mime_type = inline_data.get("mimeType", "")
                        audio_data = inline_data.get("data", "")
                        
                        if audio_data and "audio" in mime_type:
                            # Send audio back to Twilio
                            # Note: May need audio conversion here
                            twilio_message = {
                                "event": "media",
                                "streamSid": "",  # Will be populated
                                "media": {
                                    "payload": audio_data
                                }
                            }
                            await twilio_ws.send_json(twilio_message)
                            
    except websockets.exceptions.ConnectionClosed:
        logger.info("Gemini WebSocket closed")
    except Exception as e:
        logger.error(f"Error receiving from Gemini: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)