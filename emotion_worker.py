"""Local SenseVoice emotion worker for AI Helper live calls.

Run separately: SENSEVOICE_DEVICE=cpu python3 emotion_worker.py
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from funasr import AutoModel

MODEL = AutoModel(model="iic/SenseVoiceSmall", trust_remote_code=True, device=os.environ.get("SENSEVOICE_DEVICE", "cpu"))
PORT = int(os.environ.get("EMOTION_WORKER_PORT", "8010"))
TAGS = re.compile(r"<\|([A-Z]+)\|>")


def analyze(pcm):
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
    result = MODEL.generate(input=samples, fs=16000, language="auto", use_itn=False)[0]
    text = str(result.get("text", ""))
    tags = {tag.lower() for tag in TAGS.findall(text)}
    emotion = next((name for name in ("angry", "sad", "happy", "neutral") if name in tags), "neutral")
    rms = float(np.sqrt(np.mean(samples * samples)))
    energy = "high" if rms > .08 else "low" if rms < .02 else "normal"
    words = len(TAGS.sub("", text).split())
    seconds = max(len(samples) / 16000, .1)
    speech_rate = "fast" if words / seconds > 2.8 else "slow" if words / seconds < 1.3 else "normal"
    tail = samples[-4096:] - np.mean(samples[-4096:])
    correlations = [float(np.dot(tail[:-lag], tail[lag:])) for lag in range(50, 401)]
    pitch = 16000 / (50 + int(np.argmax(correlations))) if max(correlations, default=0) > 0 else 0
    return {"emotion": emotion, "pitch": "raised" if pitch > 190 else "low" if 0 < pitch < 120 else "normal", "speech_rate": speech_rate, "energy": energy}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if self.path != "/analyze" or self.headers.get("X-Sample-Rate") != "16000" or not 0 < length <= 320_000:
            self.send_error(400)
            return
        try:
            payload = json.dumps(analyze(self.rfile.read(length))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception:
            self.send_error(503)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
