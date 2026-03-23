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
| 2a | mkv | hevc | ac3 5.1 eng | srt subs | Remux to MP4, create EAC3+AAC, remove AC3, strip subs | pass (post-fix) |
| 2b | mkv | hevc | ac3 5.1 eng + aac 2.0 eng | srt subs | Remux to MP4, create EAC3, remove AC3, strip subs | pass (post-fix) |
| 2c | mkv | hevc | dts 5.1 eng | pgs subs, fonts | Remux to MP4, remove DTS, create EAC3+AAC, strip subs+fonts | pass |
| 2d | mkv | hevc | truehd 7.1 eng | pgs subs | Remux to MP4, remove TrueHD, create EAC3+AAC, strip subs | pass |
| 2e | mkv | hevc | aac 2.0 eng | ass subs, fonts | Remux to MP4, strip subs+fonts | pass |
| 2f | mkv | hevc | flac 2.0 eng | none | Remux to MP4, remove FLAC, create AAC | pass |
| 2g | mkv | hevc | ac3 2.0 eng | none | Remux to MP4, create AAC, remove AC3 (no EAC3 — <6ch) | pass (post-fix) |
| 2h | mkv | hevc | mp3 2.0 eng | none | Remux to MP4, create AAC, remove MP3 | pass (post-fix) |

### 3. MKV with non-HEVC video (needs transcode)

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 3a | mkv | h264 | ac3 5.1 eng | srt subs | Transcode to HEVC/MP4, create EAC3+AAC, remove AC3, strip subs | pass (post-fix) |
| 3b | mkv | h264 | aac 2.0 eng | none | Transcode to HEVC/MP4, keep AAC | pass |
| 3c | mkv | mpeg2 | ac3 2.0 eng | none | Transcode to HEVC/MP4, create AAC, remove AC3 | pass (post-fix) |
| 3d | mkv | vp9 | opus 2.0 eng | none | Transcode to HEVC/MP4, remove Opus, create AAC | pass |
| 3e | mkv | av1 | aac 2.0 eng | none | Software transcode to HEVC/MP4 (AV1 can't hw decode on T400), keep AAC | pass |

### 4. AVI files

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 4a | avi | h264 | mp3 2.0 eng | none | Transcode to HEVC/MP4, create AAC, remove MP3 | pass (post-fix) |
| 4b | avi | mpeg4 | ac3 5.1 eng | none | Transcode to HEVC/MP4, create EAC3+AAC, remove AC3 | pass (post-fix) |

### 5. M2TS/TS files

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 5a | m2ts | h264 | dts-hd 7.1 eng | pgs subs | Transcode to HEVC/MP4, remove DTS, create EAC3+AAC, strip subs | pass |
| 5b | ts | h264 | ac3 5.1 eng | dvb subs | Transcode to HEVC/MP4, create EAC3+AAC, remove AC3, strip subs | pass (post-fix) |

### 6. Multi-language audio

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 6a | mkv | hevc | ac3 5.1 eng + ac3 5.1 jpn | srt subs | Create EAC3+AAC (eng), remove AC3, strip subs | pass (post-fix) |
| 6b | mkv | hevc | aac 2.0 und | none | Create AAC (und pass), keep existing | pass |
| 6c | mkv | hevc | ac3 5.1 eng + aac 2.0 und | none | Create EAC3 (eng) + keep AAC (und), remove AC3 | pass (post-fix) |
| 6d | mkv | hevc | aac 2.0 jpn (no eng, no und) | none | Fallback AAC creation (cmd_ens_fb) | pass |

### 7. Many-stream files (40+ streams)

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 7a | mkv | hevc | ac3 5.1 eng | 30+ srt/ass subs, fonts | Pass 1 strips subs+fonts, creates EAC3+AAC; pass 2 removes AC3 | pass (post-fix) |
| 7b | mkv | h264 | dts 5.1 eng + ac3 5.1 eng | 40+ pgs subs | Pass 1 strips subs, removes DTS, creates EAC3+AAC; pass 2 removes AC3 | pass (post-fix) |

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
| 10a | mkv | hevc | ac3 5.1 eng + eac3 5.1 eng (orphaned) | none | Guard detects orphaned EAC3 → process, remove AC3 | pass |
| 10b | mp4 | hevc | none (video only) | none | grd_has_audio → fail_no_streams | pass |
| 10c | mkv | hevc | pcm_s24le 2.0 eng | none | Remove PCM, create AAC | pass |
| 10d | mkv | hevc | ac3 5.1 eng + mp3 2.0 eng | srt | Create EAC3+AAC, remove AC3+MP3, strip subs | pass (post-fix) |
| 10e | mp4 | hevc (hvc1) | aac 2.0 eng + mp3 2.0 eng | none | Process — guard catches unwanted MP3 | pass |
| 10f | mp4 | hevc (hvc1) | aac 2.0 eng + ac3 2.0 eng | none | Process — guard catches unwanted AC3 | pass |
| 10g | mp4 | hevc (hvc1) | aac 2.0 eng + eac3 5.1 eng | none | Skip — EAC3 surround is not unwanted | pass |
| 10h | mp4 | hevc (hvc1) | eac3 6ch kor + eac3 6ch chi (no AAC) | none | Process — grd_aud catches missing AAC, fallback AAC creation | pass |

## Known Regressions

### PR #49/#50 regression (fixed in PR #51)

**Root cause**: `cmd_rm_ac3mp3` (ffmpegCommandRemoveStreamByProperty with `ac3,mp3,dts`) was placed
in pass 1 AFTER `ffmpegCommandEnsureAudioStream`. EnsureAudioStream clones keep the source stream's
`codec_name` (e.g. "ac3"), even though `outputArgs` specify the target codec (eac3/aac). The removal
plugin checks `codec_name`, so it destroyed both originals AND clones → zero audio in output.

**Affected permutations**: ALL files needing audio creation (every row marked "post-fix" above).

**Fix**: Move `cmd_rm_ac3mp3` to a separate pass 2 (`ffs_002 → cmd_rm_ac3mp3 → ffe_002`), executed
after pass 1 completes. At that point, EnsureAudioStream clones have been executed into real streams
with correct codec_names (eac3, aac), so removing by `codec_name: "ac3"` only targets originals.

Pass 2 is a pure stream-copy remux (no transcode) — takes seconds. The pass 1 output has few streams
(video + 2-4 audio after stripping subs/data/images/incompatible audio), so probing works reliably.

### Guard chain silent passthrough (fixed in PR #62)

**Root cause**: `grd_unwanted` (checkStreamProperty) used `condition: "equals"` with comma-separated
`valuesToMatch: "ac3,dts,dca,mp3,..."`. The `checkStreamProperty` plugin with `condition: "equals"`
compares `codec_name` against the **entire string** as one value (no comma iteration) — so the guard
never matched any individual codec. Files that passed all other guards (mp4/hevc/hvc1/aac) but still
had unwanted stereo audio (mp3, ac3) were incorrectly marked "Not required."

**Affected permutations**: 10e, 10f — any MP4/HEVC/hvc1 file with AAC + unwanted stereo audio.
The test evaluator was also masking this bug by iterating comma-separated values for "equals."

**Fix**: Split unwanted audio detection into two guards:
- `grd_unwanted` — changed to `condition: "includes"` (correctly iterates comma values), with
  "ac3" removed from the list (since `"eac3".includes("ac3")` would falsely match EAC3)
- `grd_unwanted_ac3` — new node with `condition: "equals"` and single value `"ac3"` (exact match,
  safe for AC3 vs EAC3). Same fix applied to VR guard (`grd_vr_nowanted` / `grd_vr_nowanted_ac3`).

Also fixed test evaluator: `checkStreamProperty` "equals" no longer iterates comma-separated values.

### PR #51 regression (fixed in PR #53)

**Root cause**: `cmd_rm_ac3mp3` in pass 2 used `condition: "includes"` with `valuesToRemove: "ac3,mp3"`.
The plugin checks `codec_name.includes(value)` for each comma-separated value. Since `"eac3".includes("ac3")`
is true, EAC3 surround tracks were destroyed alongside AC3 originals → zero audio in output.

**Affected permutations**: All files with EAC3 in pass 2 output (2a, 2b, 2c, 2d, 6a, 6c, 7a, 7b, 10a, 10d).
Also Squid Game-type files (non-English EAC3 only) where EAC3 was the sole surviving audio.

**Fix**: Replace single `cmd_rm_ac3mp3` node with two separate exact-match nodes:
- `cmd_rm_ac3` (`valuesToRemove: "ac3"`, `condition: "equals"`) — matches AC3, NOT EAC3
- `cmd_rm_mp3` (`valuesToRemove: "mp3"`, `condition: "equals"`) — matches MP3
Chain: `ffs_002 → cmd_rm_ac3 → cmd_rm_mp3 → ffe_002`
