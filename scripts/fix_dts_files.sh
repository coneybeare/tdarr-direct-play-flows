#!/bin/bash
# Fix MKV/HEVC files with non-monotonic DTS that corrupt MP4 stream-copy.
#
# Some MKV muxers produce HEVC streams with non-monotonic decode timestamps
# (B-frame reordering artifacts). When FFmpeg stream-copies these to MP4,
# the output timing atoms are corrupted — FFprobe returns empty data and
# HandBrake can't open the file. This script re-encodes the video via
# hardware (VideoToolbox on macOS, NVENC on Linux) to produce clean
# timestamps, matching the flow's target format:
#   MP4 / HEVC hvc1 / AAC 2.0 stereo (first) / EAC3 5.1 (if surround)
#
# Usage:
#   # Process a single file
#   scripts/fix_dts_files.sh /path/to/file.mkv
#
#   # Process multiple files
#   scripts/fix_dts_files.sh /path/to/file1.mkv /path/to/file2.mkv ...
#
#   # Process files from a list (one path per line)
#   scripts/fix_dts_files.sh --from-file /tmp/dts_files.txt
#
#   # Dry run (show what would be done without encoding)
#   scripts/fix_dts_files.sh --dry-run /path/to/file.mkv
#
#   # Use with analyze_tdarr.py to find affected files:
#   python3 scripts/analyze_tdarr.py --servers --status errors --json | \
#     python3 -c "import sys,json; [print(f['_id']) for f in json.load(sys.stdin) if ...]" | \
#     xargs scripts/fix_dts_files.sh
#
# Requirements:
#   - ffmpeg and ffprobe on PATH (or set FFMPEG/FFPROBE env vars)
#   - Files accessible on local or mounted filesystem
#   - Hardware encoder: hevc_videotoolbox (macOS) or hevc_nvenc (Linux/NVIDIA)
#
# The script will:
#   1. Probe the source to detect video bitrate and surround audio
#   2. Re-encode video at matching bitrate with hardware HEVC encoder
#   3. Create AAC 2.0 stereo (first track) + EAC3 5.1 (if surround source)
#   4. Set hvc1 tag + faststart, strip subtitles/data/chapters
#   5. Verify output with ffprobe
#   6. Replace original MKV with new MP4

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────

FFMPEG="${FFMPEG:-$(command -v ffmpeg 2>/dev/null || echo ffmpeg)}"
FFPROBE="${FFPROBE:-$(command -v ffprobe 2>/dev/null || echo ffprobe)}"
LOG="${FIX_DTS_LOG:-/tmp/fix_dts_files.log}"
DRY_RUN=false

# Detect hardware encoder
detect_encoder() {
  if "$FFMPEG" -hide_banner -encoders 2>/dev/null | grep -q hevc_videotoolbox; then
    echo "hevc_videotoolbox"
  elif "$FFMPEG" -hide_banner -encoders 2>/dev/null | grep -q hevc_nvenc; then
    echo "hevc_nvenc"
  else
    echo "libx265"
  fi
}

ENCODER="${HEVC_ENCODER:-$(detect_encoder)}"

# ── Functions ─────────────────────────────────────────────────────────────────

usage() {
  sed -n '2,/^$/s/^# //p' "$0"
  exit 1
}

log() {
  echo "$*" | tee -a "$LOG"
}

process_file() {
  local src="$1"

  if [[ ! -f "$src" ]]; then
    log "[SKIP] File not found: $src"
    ((skip++))
    return
  fi

  local filename dir stem ext output tmp
  filename=$(basename "$src")
  dir=$(dirname "$src")
  stem="${filename%.*}"
  ext="${filename##*.}"
  output="${dir}/${stem}.mp4"
  tmp="${dir}/.fixing_${stem}.mp4"

  # Skip if already MP4
  if [[ "${ext,,}" == "mp4" ]]; then
    log "[SKIP] Already MP4: $filename"
    ((skip++))
    return
  fi

  # Skip if output already exists
  if [[ -f "$output" ]]; then
    log "[SKIP] Output exists: ${stem}.mp4"
    ((skip++))
    return
  fi

  log ""
  log "--- $filename ---"

  # Probe source for video bitrate and audio channels
  local probe_json
  probe_json=$("$FFPROBE" -v error -show_streams -show_format -print_format json "$src" 2>/dev/null)

  local vbitrate
  vbitrate=$(echo "$probe_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
# Try video stream bitrate first, fall back to format bitrate
for s in d.get('streams', []):
    if s.get('codec_type') == 'video':
        br = s.get('bit_rate', '')
        if br:
            print(br)
            sys.exit()
fmt_br = d.get('format', {}).get('bit_rate', '0')
print(fmt_br)
" 2>/dev/null)
  vbitrate="${vbitrate:-0}"

  local has_surround
  has_surround=$(echo "$probe_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for s in d.get('streams', []):
    if s.get('codec_type') == 'audio' and s.get('channels', 0) >= 6:
        print('true')
        sys.exit()
print('false')
" 2>/dev/null)

  local video_br_k=$(( vbitrate / 1000 ))
  if (( video_br_k < 100 )); then
    video_br_k=1000  # fallback for missing bitrate
  fi

  log "  Encoder: $ENCODER"
  log "  Video bitrate: ${video_br_k} kbps"
  log "  Surround: $has_surround"

  if $DRY_RUN; then
    log "  [DRY RUN] Would encode to: $output"
    ((skip++))
    return
  fi

  # Build FFmpeg command
  local cmd=("$FFMPEG" -y -i "$src")

  # Video: re-encode with hardware HEVC
  cmd+=(-map 0:v:0 -c:v "$ENCODER" -b:v "${video_br_k}k" -tag:v hvc1)

  if [[ "$has_surround" == "true" ]]; then
    # AAC stereo (first, for basic client compat) + EAC3 5.1 surround
    cmd+=(-map 0:a:0 -c:a:0 aac -ac:a:0 2 -b:a:0 192k)
    cmd+=(-map 0:a:0 -c:a:1 eac3 -ac:a:1 6 -b:a:1 384k)
  else
    # Stereo only: AAC stereo
    cmd+=(-map 0:a:0 -c:a:0 aac -ac:a:0 2 -b:a:0 192k)
  fi

  # Strip subtitles, data streams, chapters; enable faststart
  cmd+=(-sn -dn -movflags +faststart -map_chapters -1)
  cmd+=("$tmp")

  log "  Encoding..."
  local start_time end_time elapsed
  start_time=$(date +%s)

  if "${cmd[@]}" >> "$LOG" 2>&1; then
    end_time=$(date +%s)
    elapsed=$(( end_time - start_time ))

    # Verify output is readable
    if "$FFPROBE" -v error -show_format -print_format json "$tmp" 2>/dev/null | grep -q '"nb_streams"'; then
      local orig_size new_size ratio
      orig_size=$(stat -f%z "$src" 2>/dev/null || stat -c%s "$src" 2>/dev/null)
      new_size=$(stat -f%z "$tmp" 2>/dev/null || stat -c%s "$tmp" 2>/dev/null)
      ratio=$(( new_size * 100 / orig_size ))

      log "  Size: $(( new_size / 1048576 )) MB (${ratio}% of $(( orig_size / 1048576 )) MB)"
      log "  Time: ${elapsed}s"

      mv "$tmp" "$output"
      rm -f "$src"
      log "  [OK] Replaced with MP4"
      ((ok++))
    else
      log "  [FAIL] Output not readable by ffprobe"
      rm -f "$tmp"
      ((fail++))
    fi
  else
    log "  [FAIL] FFmpeg error (see $LOG)"
    rm -f "$tmp"
    ((fail++))
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

files=()

while (( $# > 0 )); do
  case "$1" in
    --dry-run)   DRY_RUN=true; shift ;;
    --from-file) shift; while IFS= read -r line; do [[ -n "$line" ]] && files+=("$line"); done < "$1"; shift ;;
    --help|-h)   usage ;;
    *)           files+=("$1"); shift ;;
  esac
done

if (( ${#files[@]} == 0 )); then
  echo "Error: no files specified. Use --help for usage." >&2
  exit 1
fi

ok=0
fail=0
skip=0

log "=== Fix DTS Files — $(date) ==="
log "Encoder: $ENCODER"
log "Files: ${#files[@]}"

for f in "${files[@]}"; do
  process_file "$f"
done

log ""
log "=== Done ==="
log "  OK: $ok | Failed: $fail | Skipped: $skip"
log "  Log: $LOG"
