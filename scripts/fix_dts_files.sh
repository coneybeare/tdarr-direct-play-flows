#!/bin/bash
# Fix MKV/HEVC files with non-monotonic DTS that corrupt MP4 stream-copy.
#
# Some MKV muxers produce HEVC streams with non-monotonic decode timestamps
# (B-frame reordering artifacts). When FFmpeg stream-copies these to MP4,
# the output timing atoms are corrupted — FFprobe returns empty data and
# HandBrake can't open the file. This script re-encodes the video to
# produce clean timestamps, matching the flow's target format:
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
#   - Preferred: hardware HEVC encoder hevc_videotoolbox (macOS) or
#     hevc_nvenc (Linux/NVIDIA); falls back to libx265 if unavailable
#
# The script will:
#   1. Probe the source to detect video bitrate and surround audio
#   2. Re-encode video at matching bitrate (hardware when available,
#      otherwise libx265)
#   3. Create AAC 2.0 stereo (first track) + EAC3 5.1 (if surround source)
#   4. Set hvc1 tag + faststart, strip subtitles/data/chapters
#   5. Verify output with ffprobe
#   6. Replace original MKV with new MP4

set -uo pipefail

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

  local filename dir stem ext output tmp local_raw local_fast
  filename=$(basename "$src")
  dir=$(dirname "$src")
  stem="${filename%.*}"
  ext=$(printf '%s' "${filename##*.}" | tr '[:upper:]' '[:lower:]')
  output="${dir}/${stem}.mp4"
  tmp="${dir}/.fixing_${stem}.mp4"
  # Encode to local temp dir to avoid SMB +faststart re-open failure
  local_raw="$(mktemp "${TMPDIR:-/tmp}/fix_dts_XXXXXXXX.mp4")" || {
    log "[FAIL] mktemp failed for raw temp file (src: $filename)"
    ((fail++))
    return
  }
  local_fast="$(mktemp "${TMPDIR:-/tmp}/fix_dts_fast_XXXXXXXX.mp4")" || {
    log "[FAIL] mktemp failed for faststart temp file (src: $filename)"
    rm -f "$local_raw"
    ((fail++))
    return
  }

  # Ensure intermediates are cleaned up on interruption
  cleanup_local_temp() {
    rm -f "$local_raw" "$local_fast"
  }
  trap cleanup_local_temp INT TERM EXIT

  # Skip if already MP4
  if [[ "$ext" == "mp4" ]]; then
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

  # Probe source for video bitrate (no python dependency)
  local vbitrate
  vbitrate=$("$FFPROBE" -v error -select_streams v:0 \
    -show_entries stream=bit_rate \
    -of default=nokey=1:noprint_wrappers=1 \
    "$src" 2>/dev/null | head -n1) || true

  # Fall back to container bitrate if video stream bitrate is missing
  if [[ -z "${vbitrate:-}" || "$vbitrate" == "N/A" ]]; then
    vbitrate=$("$FFPROBE" -v error \
      -show_entries format=bit_rate \
      -of default=nokey=1:noprint_wrappers=1 \
      "$src" 2>/dev/null | head -n1) || true
  fi
  vbitrate="${vbitrate:-0}"

  # Detect audio: check for any audio streams
  local has_audio=false
  local audio_channels
  audio_channels=$("$FFPROBE" -v error -select_streams a \
    -show_entries stream=channels \
    -of default=nokey=1:noprint_wrappers=1 \
    "$src" 2>/dev/null) || true

  if [[ -n "$audio_channels" ]]; then
    has_audio=true
  fi

  # Detect surround: find first audio stream with 6+ channels
  local has_surround=false
  local surround_index=""
  if [[ "$has_audio" == "true" ]]; then
    local idx=0
    while IFS= read -r ch; do
      if [[ "$ch" =~ ^[0-9]+$ ]] && (( ch >= 6 )); then
        has_surround=true
        surround_index=$idx
        break
      fi
      ((idx++))
    done <<< "$audio_channels"
  fi

  local video_br_k=$(( vbitrate / 1000 ))
  if (( video_br_k < 100 )); then
    video_br_k=1000  # fallback for missing bitrate
  fi

  log "  Encoder: $ENCODER"
  log "  Video bitrate: ${video_br_k} kbps"
  log "  Audio: $has_audio | Surround: $has_surround"

  if [[ "$has_audio" == "false" ]]; then
    log "  [SKIP] No audio streams — video-only files not supported"
    ((skip++))
    return
  fi

  if $DRY_RUN; then
    log "  [DRY RUN] Would encode to: $output"
    ((skip++))
    return
  fi

  # Build FFmpeg command
  local cmd=("$FFMPEG" -y -i "$src")

  # Video: re-encode with HEVC
  cmd+=(-map 0:v:0 -c:v "$ENCODER" -b:v "${video_br_k}k" -tag:v hvc1)

  if [[ "$has_surround" == "true" ]]; then
    # AAC stereo from first audio (for basic client compat)
    cmd+=(-map 0:a:0 -c:a:0 aac -ac:a:0 2 -b:a:0 192k)
    # EAC3 5.1 from the surround stream (may differ from first audio)
    cmd+=(-map "0:a:${surround_index}" -c:a:1 eac3 -ac:a:1 6 -b:a:1 384k)
  else
    # Stereo only: AAC stereo
    cmd+=(-map 0:a:0 -c:a:0 aac -ac:a:0 2 -b:a:0 192k)
  fi

  # Strip subtitles, data streams, chapters (faststart applied in step 2)
  cmd+=(-sn -dn -map_chapters -1)
  cmd+=("$local_raw")

  log "  Encoding to local temp..."
  local start_time end_time elapsed
  start_time=$(date +%s)

  if "${cmd[@]}" >> "$LOG" 2>&1; then
    end_time=$(date +%s)
    elapsed=$(( end_time - start_time ))
    log "  Encode time: ${elapsed}s"

    # Step 2: Apply faststart via local stream-copy (avoids SMB re-open bug)
    log "  Applying faststart..."
    if "$FFMPEG" -y -i "$local_raw" -c copy -movflags +faststart "$local_fast" >> "$LOG" 2>&1; then
      rm -f "$local_raw"
    else
      log "  [WARN] faststart failed, using non-faststart output"
      mv "$local_raw" "$local_fast"
    fi

    # Verify output is readable
    if "$FFPROBE" -v error -show_format -print_format json "$local_fast" 2>/dev/null | grep -q '"nb_streams"'; then
      local orig_size new_size
      orig_size=$(stat -f%z "$src" 2>/dev/null || stat -c%s "$src" 2>/dev/null || echo 0)
      new_size=$(stat -f%z "$local_fast" 2>/dev/null || stat -c%s "$local_fast" 2>/dev/null || echo 0)

      if [[ "$orig_size" =~ ^[0-9]+$ ]] && [[ "$new_size" =~ ^[0-9]+$ ]] && (( orig_size > 0 )); then
        local ratio=$(( new_size * 100 / orig_size ))
        log "  Size: $(( new_size / 1048576 )) MB (${ratio}% of $(( orig_size / 1048576 )) MB)"
      elif [[ "$new_size" =~ ^[0-9]+$ ]]; then
        log "  Size: $(( new_size / 1048576 )) MB"
      fi

      # Move to final location on SMB mount
      log "  Moving to destination..."
      if mv "$local_fast" "$tmp"; then
        if mv "$tmp" "$output"; then
          rm -f "$src"
          log "  [OK] Replaced with MP4"
          ((ok++))
        else
          log "  [FAIL] Failed moving temp file to destination"
          rm -f "$tmp"
          ((fail++))
        fi
      else
        log "  [FAIL] Failed moving faststart file to destination"
        rm -f "$local_fast"
        ((fail++))
      fi
    else
      log "  [FAIL] Output not readable by ffprobe"
      rm -f "$local_raw" "$local_fast"
      ((fail++))
    fi
  else
    log "  [FAIL] FFmpeg error (see $LOG)"
    rm -f "$local_raw"
    ((fail++))
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

files=()

while (( $# > 0 )); do
  case "$1" in
    --dry-run)   DRY_RUN=true; shift ;;
    --from-file)
      shift
      if (( $# == 0 )) || [[ "$1" == -* ]]; then
        echo "Error: --from-file requires a filename argument" >&2
        exit 1
      fi
      if [[ ! -f "$1" ]]; then
        echo "Error: --from-file '$1' is not a readable file" >&2
        exit 1
      fi
      while IFS= read -r line; do [[ -n "$line" ]] && files+=("$line"); done < "$1"
      shift
      ;;
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
