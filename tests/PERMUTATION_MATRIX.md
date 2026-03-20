# Input Permutation Matrix

Every logic change to the flow must be validated against these known input permutations.
When a new permutation is discovered, add it here and verify the flow handles it correctly.

## Legend

- **Container**: Input file container format
- **Video**: Video codec in source
- **Audio**: Audio codecs present (with channel layout)
- **Other**: Subtitles, attachments, data streams
- **Expected outcome**: What the flow should produce
- **Status**: Last verified state (pass/fail/untested)

## Permutations

### 1. MP4/HEVC already optimal

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 1a | mp4 | hevc (hvc1) | aac 2.0 eng | none | Skip (fl_noop) — already optimal | pass |
| 1b | mp4 | hevc (hvc1) | aac 2.0 eng + eac3 5.1 eng | none | Skip (fl_noop) — already optimal | pass |
| 1c | mp4 | hevc (hev1) | aac 2.0 eng | none | Process (retag to hvc1 only) | pass |

### 2. MKV with HEVC video

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 2a | mkv | hevc | ac3 5.1 eng | srt subs | Remux to MP4, create EAC3+AAC, strip subs, AC3 kept | pass (post-fix) |
| 2b | mkv | hevc | ac3 5.1 eng + aac 2.0 eng | srt subs | Remux to MP4, create EAC3, strip subs, AC3 kept | pass (post-fix) |
| 2c | mkv | hevc | dts 5.1 eng | pgs subs, fonts | Remux to MP4, remove DTS, create EAC3+AAC, strip subs+fonts | pass |
| 2d | mkv | hevc | truehd 7.1 eng | pgs subs | Remux to MP4, remove TrueHD, create EAC3+AAC, strip subs | pass |
| 2e | mkv | hevc | aac 2.0 eng | ass subs, fonts | Remux to MP4, strip subs+fonts | pass |
| 2f | mkv | hevc | flac 2.0 eng | none | Remux to MP4, remove FLAC, create AAC | pass |
| 2g | mkv | hevc | ac3 2.0 eng | none | Remux to MP4, create AAC, AC3 kept (no EAC3 — <6ch) | pass (post-fix) |
| 2h | mkv | hevc | mp3 2.0 eng | none | Remux to MP4, create AAC, MP3 kept | pass (post-fix) |

### 3. MKV with non-HEVC video (needs transcode)

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 3a | mkv | h264 | ac3 5.1 eng | srt subs | Transcode to HEVC/MP4, create EAC3+AAC, strip subs, AC3 kept | pass (post-fix) |
| 3b | mkv | h264 | aac 2.0 eng | none | Transcode to HEVC/MP4, keep AAC | pass |
| 3c | mkv | mpeg2 | ac3 2.0 eng | none | Transcode to HEVC/MP4, create AAC, AC3 kept | pass (post-fix) |
| 3d | mkv | vp9 | opus 2.0 eng | none | Transcode to HEVC/MP4, remove Opus, create AAC | pass |
| 3e | mkv | av1 | aac 2.0 eng | none | Transcode to HEVC/MP4, keep AAC | pass |

### 4. AVI files

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 4a | avi | h264 | mp3 2.0 eng | none | Transcode to HEVC/MP4, create AAC, MP3 kept | pass (post-fix) |
| 4b | avi | mpeg4 | ac3 5.1 eng | none | Transcode to HEVC/MP4, create EAC3+AAC, AC3 kept | pass (post-fix) |

### 5. M2TS/TS files

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 5a | m2ts | h264 | dts-hd 7.1 eng | pgs subs | Transcode to HEVC/MP4, remove DTS, create EAC3+AAC, strip subs | pass |
| 5b | ts | h264 | ac3 5.1 eng | dvb subs | Transcode to HEVC/MP4, create EAC3+AAC, strip subs, AC3 kept | pass (post-fix) |

### 6. Multi-language audio

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 6a | mkv | hevc | ac3 5.1 eng + ac3 5.1 jpn | srt subs | Create EAC3+AAC (eng), AC3 kept, strip subs | pass (post-fix) |
| 6b | mkv | hevc | aac 2.0 und | none | Create AAC (und pass), keep existing | pass |
| 6c | mkv | hevc | ac3 5.1 eng + aac 2.0 und | none | Create EAC3 (eng) + keep AAC (und), AC3 kept | pass (post-fix) |
| 6d | mkv | hevc | aac 2.0 jpn (no eng, no und) | none | Fallback AAC creation (cmd_ens_fb) | pass |

### 7. Many-stream files (40+ streams)

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 7a | mkv | hevc | ac3 5.1 eng | 30+ srt/ass subs, fonts | Single-pass: strip subs+fonts, create EAC3+AAC | pass (post-fix) |
| 7b | mkv | h264 | dts 5.1 eng + ac3 5.1 eng | 40+ pgs subs | Single-pass: strip all subs, remove DTS, create EAC3+AAC | pass |

### 8. DoVi / HDR files

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 8a | mkv | hevc (dvh1) | eac3 5.1 eng | none | Skip (fl_noop) — DoVi, cannot safely process | pass |
| 8b | mp4 | hevc (dvhe) | aac 2.0 eng | none | Skip (fl_noop) — DoVi | pass |

### 9. VR files (in /Virtual Reality/ path)

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 9a | mkv | hevc | aac 2.0 eng | spherical metadata | VR branch: remux to MP4, preserve spherical, no EAC3 | pass |
| 9b | mkv | h264 | aac 2.0 eng | spherical metadata | VR branch: transcode to HEVC, preserve spherical | pass |

### 10. Edge cases

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 10a | mkv | hevc | ac3 5.1 eng + eac3 5.1 eng (orphaned) | none | Guard detects orphaned EAC3 → process | pass |
| 10b | mp4 | hevc | none (video only) | none | grd_has_audio → fail_no_streams | pass |
| 10c | mkv | hevc | pcm_s24le 2.0 eng | none | Remove PCM, create AAC | pass |
| 10d | mkv | hevc | ac3 5.1 eng + mp3 2.0 eng | srt | Create EAC3+AAC, AC3+MP3 kept, strip subs | pass (post-fix) |

## Known Regressions

### PR #50 regression (fixed in PR #51)

**Root cause**: `cmd_rm_ac3mp3` (ffmpegCommandRemoveStreamByProperty with `ac3,mp3,dts`) was placed AFTER
`ffmpegCommandEnsureAudioStream` in a single-pass pipeline. EnsureAudioStream clones keep the source
stream's `codec_name` (e.g. "ac3"), even though `outputArgs` specify the target codec (eac3/aac).
The removal plugin checks `codec_name`, so it destroyed both originals AND clones → zero audio in output.

**Affected permutations**: ALL files needing audio creation (every row marked "post-fix" above).

**Fix**: Removed `cmd_rm_ac3mp3` entirely. AC3 and MP3 tracks are kept in output alongside newly created
EAC3/AAC. The reorder plugin orders them correctly (aac first, then eac3, then ac3/mp3).

**Trade-off**: Output files retain original AC3/MP3 tracks (~345MB for 2hr AC3 5.1). This is acceptable
for direct play (AC3 is universally supported) and prevents the clone-destruction bug.
