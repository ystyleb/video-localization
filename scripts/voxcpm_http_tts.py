from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib import request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call an OpenAI-compatible local VoxCPM TTS server and save the audio output."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL")
    parser.add_argument("--text-file", type=Path, required=True, help="UTF-8 text file to synthesize")
    parser.add_argument("--output", type=Path, required=True, help="Output audio path")
    parser.add_argument("--voice", default="", help="Optional cached voice name")
    parser.add_argument("--response-format", default="wav", help="Audio format, usually wav")
    parser.add_argument("--max-length", type=int, default=2048, help="Maximum generation length")
    parser.add_argument("--cfg-value", type=float, default=2.0, help="Classifier-free guidance value")
    parser.add_argument(
        "--inference-timesteps",
        type=int,
        default=10,
        help="Inference diffusion steps",
    )
    parser.add_argument("--prompt-wav-path", default="", help="Optional prompt wav path")
    parser.add_argument("--prompt-text", default="", help="Optional prompt transcription")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    payload = {
        "model": "voxcpm-0.5b",
        "input": args.text_file.read_text(encoding="utf-8").strip(),
        "response_format": args.response_format,
        "max_length": args.max_length,
        "cfg_value": args.cfg_value,
        "inference_timesteps": args.inference_timesteps,
    }
    if args.voice:
        payload["voice"] = args.voice
    if args.prompt_wav_path:
        payload["prompt_wav_path"] = args.prompt_wav_path
    if args.prompt_text:
        payload["prompt_text"] = args.prompt_text

    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{args.base_url.rstrip('/')}/v1/audio/speech",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req) as resp:  # noqa: S310 - controlled local endpoint
        audio = resp.read()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(audio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
