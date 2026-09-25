# Media File Tools

Scripts for processing files received via any transport (Telegram, Matrix, API).

## Common Commands

```bash
python tools/media_tools/list_files.py --limit 20
python tools/media_tools/list_files.py --type image
python tools/media_tools/list_files.py --date 2026-01-15
python tools/media_tools/file_info.py --file /absolute/path/to/file
python tools/media_tools/read_document.py --file /absolute/path/to/doc.pdf
python tools/media_tools/transcribe_audio.py --file /absolute/path/to/audio.ogg
python tools/media_tools/process_video.py --file /absolute/path/to/video.mp4
```

## File-Type Routing

- image/photo: inspect directly
- audio/voice: transcribe first
- document/PDF: extract text
- video: frames + transcript
- sticker: acknowledge naturally

## Dependencies

- always available: `file_info.py`, `list_files.py`
- PDF parsing: `pypdf`
- YAML listing: `pyyaml`
- audio transcription: OpenAI API key or local Whisper variants
- video processing: `ffmpeg`

## Response UX

After processing, offer concise next actions (optional buttons) when helpful.
