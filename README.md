# tdarr-direct-play-flows

[![Validate Flows](https://github.com/coneybeare/tdarr-direct-play-flows/actions/workflows/test.yml/badge.svg)](https://github.com/coneybeare/tdarr-direct-play-flows/actions/workflows/test.yml)

Tdarr flows optimized to maximize direct-play compatibility on iOS, macOS, Plex, and web (video.js) clients — while reducing file size using HEVC (H.265).

Designed for a **Synology NAS** with an **NVIDIA T400 GPU** running Tdarr in Docker via Portainer, but adaptable to any NVENC-capable system.

---

## What These Flows Do

| Output Characteristic | Reason |
|---|---|
| **HEVC (H.265) video** | 30–50% smaller than H.264 at same quality; natively supported on Apple Silicon, A-series, and modern GPUs |
| **MP4 container** | Best compatibility with iOS, Safari, Plex Web, and video.js; better than MKV for direct play |
| **`hvc1` codec tag** | Required for iOS/macOS/tvOS to hardware-decode HEVC; FFmpeg defaults to `hev1` which Apple rejects |
| **`-movflags +faststart`** | Moves the MP4 moov atom to the front, enabling streaming to start before full download |
| **Stereo AAC 2.0 as first audio track** | Universal fallback audio: plays on every client without transcoding (video.js, Plex Web, Safari, etc.) |
| **EAC3 5.1 surround** | Dolby Digital Plus direct-plays on Plex iOS, Apple TV; replaces DTS which forces transcoding |
| **Subtitles removed** | Image-based subtitles (PGS) force server transcoding; removing all subs maximizes direct play |
| **Commentary tracks removed** | Director commentary, audio descriptions, SDH tracks removed (opt-in; saves space, reduces confusion) |
| **Data/attachment streams removed** | Fonts, cover art, metadata streams removed — incompatible with MP4 and irrelevant for playback |
| **Stream reordering** | Stereo AAC first, then EAC3/AC3 surround, ensuring simple clients pick the right track automatically |

---

## Flows

### [Flow 01 — Direct Play Optimizer (Full Pipeline)](flows/01_hevc_mp4_direct_play.json)

**Use this for most libraries.** The primary flow. Handles everything end-to-end.

**Processing steps:**
1. **Configuration** — set flow variables at the top (commentary removal, Plex/arr notifications)
2. **Guard checks** — skips files already in MP4+HEVC+stereo AAC (marks as done without re-encoding)
3. **Low-bitrate skip** — skips files under 1 Mbps (already highly compressed; re-encoding won't help)
4. **Health check** — detects corrupt/unreadable source files before processing
5. Set container → MP4 (`forceConform` removes PGS subtitles and incompatible streams automatically)
6. Remove all subtitles
7. Remove commentary/description/SDH audio tracks (opt-in via `remove_commentary` variable)
8. Remove data streams
9. **NVENC availability check** — routes to GPU encoder or software fallback (libx265)
10. **Resolution-tiered encoding** — SD=QP26, 1080p=QP24, 4K=QP28 (stream-copies existing HEVC)
11. Add `-tag:v hvc1` and `-movflags +faststart`
12. Add EAC3 5.1 surround @ 384k (dual-pass: `eng` + `und` language tags)
13. Add stereo AAC 2.0 @ 192k (dual-pass: `eng` + `und` language tags)
14. Remove incompatible audio codecs (TrueHD, FLAC, Vorbis, WMA, Opus, **DTS**)
15. Reorder streams (stereo AAC first)
16. Execute FFmpeg
17. Validate output size (≤120% of original; sends to Review if larger)
18. Validate output duration (98–102% of original; catches truncated/corrupt outputs)
19. Replace original file
20. Notify Plex (opt-in via `enable_plex_notify` variable)
21. Notify Radarr/Sonarr (opt-in via `enable_arr_notify` variable)

---

### [Flow 02 — Audio Fix: Stereo AAC 2.0 + EAC3 5.1](flows/02_audio_stereo_add_only.json)

**Lightweight supplementary flow.** Use when files are already HEVC+MP4 but missing a stereo AAC track.

- Skips any file that is not already MP4+HEVC (those should use Flow 01)
- Skips any file that already has stereo AAC (guard; use Flow 01 to also add EAC3 to those)
- Health check before processing
- Removes commentary/description/SDH tracks (opt-in)
- Adds EAC3 5.1 surround (if surround source exists)
- Adds stereo AAC 2.0
- Removes DTS and other incompatible codecs
- Reorders streams; validates duration
- Optional Plex/arr notifications
- Stream-copies video — very fast (seconds, not minutes)

**When to use:** Run this as a secondary pass on libraries that were previously converted to HEVC/MP4 by other tools but may not have the stereo AAC downmix.

---

### [Flow 03 — HDR-Aware Transcode](flows/03_hdr_aware_transcode.json)

**Use for libraries with 4K HDR content** (HDR10, HLG, Dolby Vision).

Re-encoding HDR video without special tooling strips the HDR metadata, resulting in washed-out colors. This flow detects HDR automatically and routes it differently:

| Content | Action |
|---|---|
| **HDR** (HDR10, HLG, Dolby Vision) | Stream-copy video only; apply all container/audio/EAC3/tag fixes |
| **SDR** | Full HEVC NVENC transcode with resolution-tiered quality (same as Flow 01) |

Both paths include: health check, commentary removal (opt-in), EAC3 5.1 + stereo AAC, DTS removal, duration validation, and optional Plex/arr notifications.

**Limitation:** Dolby Vision profile 4/7 (dual-layer MKV) cannot be losslessly remuxed to MP4 without `dovi_tool`. This flow handles single-layer DV (profile 5/8) via stream copy correctly.

---

## Importing Into Tdarr

1. Open your Tdarr instance web UI
2. Navigate to **Flows** in the sidebar
3. Click **Import Flow** (or the `+` / upload button)
4. Select the JSON file from the `flows/` directory
5. The flow will appear in your Flows list

**To assign a flow to a library:**
1. Go to **Libraries**
2. Select your library
3. Under **Transcode Options** → **Flow**, select the imported flow
4. Set **Workers** to your GPU transcode worker count

---

## Configuration Guide

All configurable values are set directly on individual nodes in Tdarr's visual flow editor. At the top of every flow are **Set Variable** nodes — click them to change the flow's behavior without touching the pipeline.

### Flow Variables (top of every flow)

| Variable | Default | Description |
|---|---|---|
| `remove_commentary` | `true` | Remove director commentary, audio description, and SDH audio tracks by title keyword match. Set to `false` to keep all audio tracks. |
| `enable_plex_notify` | `false` | Trigger a Plex library refresh after each file is replaced. **Must also configure the `Notify Plex` webRequest node** with your Plex IP and token. |
| `enable_arr_notify` | `false` | Notify Radarr or Sonarr after file replacement. **Must also configure the `Notify Arr` node** with your arr URL and API key. |

### Critical Settings to Review Before Running

| Setting | Node | Default | Guidance |
|---|---|---|---|
| `ffmpegQuality` | Set Video Encoder (SD) | `26` | QP for NVENC on SD (≤720p) content. |
| `ffmpegQuality` | Set Video Encoder (1080p) | `24` | QP for NVENC on 1080p content. Recommended starting point. |
| `ffmpegQuality` | Set Video Encoder (4K+) | `28` | QP for NVENC on 4K+ content. 4K is already detailed; higher QP is fine. |
| `ffmpegQuality` | Set Video Encoder (SW) | `22` | CRF for libx265 software fallback. Different scale from QP. |
| `ffmpegPreset` | Set Video Encoder | `slow` | NVENC preset. `slow` = better quality (maps to p6). `medium` = faster. Don't go faster than `medium`. |
| `bitrate` | Ensure EAC3 5.1 | `384k` | EAC3 surround bitrate. 320k is fine; 448k is slightly better. |
| `bitrate` | Ensure Stereo AAC | `192k` | Stereo AAC bitrate. 128k is fine; 192k is noticeably better on headphones. |
| `language` | Ensure Audio nodes | `eng` / `und` | Change to your library's primary language code (e.g., `jpn`, `spa`, `fre`). Add more EnsureAudioStream nodes for multilingual libraries. |
| `extensions` | Guard: Container check | `mp4,m4v` | Add any other MP4-family extensions your library uses. |
| `lessThan` | Validate File Size | `120` | Allows up to 20% growth. Increase to 150 if you see false positives on short clips. |
| `url` | Notify Plex | placeholder | Set to `http://YOUR_PLEX_IP:32400/library/sections/all/refresh?X-Plex-Token=YOUR_TOKEN` |
| `arr_host` | Notify Arr | placeholder | Set to your Radarr or Sonarr URL (e.g., `http://192.168.1.10:7878`) |
| `arr_api_key` | Notify Arr | placeholder | Found in Radarr/Sonarr under Settings → General → API Key |

---

## Key Design Decisions

### Why EAC3 Instead of Keeping DTS?

DTS (`dca`) is a high-quality surround format, but it forces Plex iOS to transcode audio — defeating the goal of direct play. The flows now:

1. Add an EAC3 5.1 track (Dolby Digital Plus) downmixed from the DTS source via `ffmpegCommandEnsureAudioStream`
2. Then remove DTS from the `valuesToRemove` list in the audio removal node

Result: files end up with AAC 2.0 stereo + EAC3 5.1 surround, which direct-plays on essentially all Plex clients including iOS.

**To keep DTS instead:** Remove `dca` from `valuesToRemove` in the `Remove Incompatible Audio` node. You can also remove the EAC3 `EnsureAudioStream` nodes if you don't want EAC3 added.

### Why NVENC with a Software Fallback?

The NVIDIA T400 is limited to 4 concurrent NVENC encode sessions. When all sessions are occupied or the GPU is unavailable, the flow automatically falls back to libx265 software encoding instead of hard-failing. This keeps the queue moving on busy systems.

### Why Resolution-Tiered QP?

NVENC QP (Quantization Parameter) is not equivalent across resolutions. A QP of 24 applied to 4K produces a much larger file than QP 24 on 480p, because there's more information to encode. The tiered approach balances quality and file size across your library:

- SD content doesn't need as much data to look good → higher QP (26)
- 1080p is the sweet spot → balanced QP (24)
- 4K is already very detailed → can use higher QP (28) without visible quality loss

### Should I Remove Subtitles?

Flow 01 and 03 remove **all** subtitles. This is the most aggressive approach for direct play because:
- PGS/VOBSUB (image-based) always force Plex to transcode for iOS/Web clients
- ASS/SRT text subs cause transcoding on many clients

If you want to **keep text subtitles** instead:
1. In the flow editor, delete the `Remove All Subtitles` node
2. Add a `ffmpegCommandRemoveStreamByProperty` node targeting `codec_name` with values `hdmv_pgs_subtitle,pgssub,dvd_subtitle,dvb_subtitle,dvb_teletext` (image-based only)

### Should I Remove Commentary Tracks?

Commentary tracks are removed by default via the `remove_commentary` variable (`true`). These are identified by keywords in the stream's title tag: `commentary`, `description`, `descriptive`, `sdh`, `hearing impaired`, `hearing-impaired`, `audio description`.

**To disable:** Set `remove_commentary` to `false` in the `Set Variable: remove_commentary` node at the top of the flow.

**To customize keywords:** Edit the `valuesToRemove` field in the `Remove Commentary` node.

---

## Hardware Notes (Synology DS1819+ / NVIDIA T400)

The T400 is a **Turing-architecture** GPU with:
- Full NVENC H.265 (HEVC) encoding support
- NVDEC hardware decoding for H.264 and HEVC
- 4 simultaneous NVENC encode sessions (T400 limit)

**Tdarr NVENC setup in Docker:**
- The container needs `--runtime=nvidia` or the NVIDIA Container Toolkit
- Set `hardwareType: nvenc` in the video encoder node
- Set worker tags to route transcode jobs to the GPU node
- Limit concurrent transcode workers to 2–3 to avoid GPU session limits

**NVENC Quality Settings:**
The T400 uses QP (Quantization Parameter) for quality, not CRF like software encoders. `ffmpegQuality: 24` maps to QP 24. For reference:
- QP 20: High quality (~90% of lossless visual quality)
- QP 24: Good quality, good compression (recommended for SDR 1080p)
- QP 26: Efficient, slight visible quality reduction on complex scenes
- QP 28: Storage-efficient, noticeable on fast motion

---

## Flow Architecture Decision Guide

```
Does your library contain 4K HDR content?
  YES → Use Flow 03 (HDR-Aware)
  NO  → Use Flow 01 (Full Pipeline)

Are there already-HEVC MP4 files missing stereo AAC?
  YES → Run Flow 02 (Audio Fix) as a secondary pass
  NO  → Flow 01/03 handles everything

Multilingual library (non-English primary)?
  → Edit the 'language' field in EnsureAudioStream nodes
  → Add additional EnsureAudioStream nodes for each language

Want Plex/arr notifications?
  → Set enable_plex_notify / enable_arr_notify to 'true'
  → Configure the Notify nodes with your URLs and API keys
```

---

## Troubleshooting

**Files going to "Needs Review" frequently:**
- Increase `ffmpegQuality` (e.g., 26–28) to reduce output size
- Check if source files are already highly compressed (HEVC at low bitrate)
- Short clips (<5 minutes) may have proportionally large container overhead
- Increase `lessThan` threshold from 120 to 150 for those libraries

**Duration validation failures:**
- Usually indicates a corrupt source file (the health check should have caught it)
- Can happen with files that have variable frame rate or unusual timecodes
- Increase the `lessThan`/`greaterThan` range slightly (e.g., 95–105) for problematic libraries

**NVENC errors / GPU not found:**
- Verify NVIDIA Container Toolkit is installed on the Synology host
- Check Portainer container has `--runtime=nvidia` or `--gpus all`
- In Tdarr, verify the worker node shows GPU hardware available
- The software fallback (libx265) will be used automatically if NVENC is unavailable

**Stereo AAC not being added:**
- Check if audio language tags differ from `eng`/`und` (use `ffprobe` to inspect)
- Add additional `ffmpegCommandEnsureAudioStream` nodes with your language code

**EAC3 not being added:**
- EAC3 5.1 requires a surround (5.1 or 7.1) source track to downmix from
- Files with only stereo audio cannot be upsampled to surround — this is expected
- Use `ffprobe` to verify channel counts on the source file

**DTS/TrueHD causing Plex transcoding:**
- DTS (`dca`) is now included in the default `valuesToRemove` list and replaced with EAC3
- If still seeing DTS, verify the `Remove Incompatible Audio` node includes `dca`
- TrueHD (`truehd`) is also in the removal list by default

**hvc1 tag not being applied:**
- Verify the `ffmpegCommandCustomArguments` node has `-tag:v hvc1 -movflags +faststart` in `outputArguments`
- Confirm the video stream is HEVC (the tag has no effect on other codecs)

**Commentary tracks not being removed:**
- Verify `remove_commentary` variable is set to `true`
- Check the stream title in the source file with `ffprobe` — the keyword must match
- Add custom keywords to the `valuesToRemove` field in the `Remove Commentary` node

**Plex/arr notifications not firing:**
- Set `enable_plex_notify` / `enable_arr_notify` to `true` in the Set Variable nodes
- Configure the `Notify Plex` webRequest URL with your actual Plex IP and token
- Configure the `Notify Arr` node with your actual arr URL and API key
- Test Plex token: Plex Settings → Account → Privacy → Show Plex Token
