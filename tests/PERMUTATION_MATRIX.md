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
| 3d | mkv | vp9 | opus 2.0 eng | none | Transcode to HEVC/MP4, create AAC 2.0 from Opus in pass 1, remove Opus | pass |
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
| 6e | mkv | h264 | dts 5.1 swe (no eng, no und) | srt subs | Known limitation: EnsureAudioStream language fallback uses "en" (loadDefaultValues replaces ""), misses non-eng/non-und → failFlow after pass 2 removes DTS | fail (known) |
| 6f | mp4 | hevc (hvc1) | ac3 5.1 cze (no eng, no und) | none | Same limitation as 6e — AC3 removed in pass 2, no AAC/EAC3 created | fail (known) |

### 7. Many-stream files (40+ streams)

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 7a | mkv | hevc | ac3 5.1 eng | 30+ srt/ass subs, fonts | Pass 1 strips subs+fonts, creates EAC3+AAC; pass 2 removes AC3 | pass (post-fix) |
| 7b | mkv | h264 | dts 5.1 eng + ac3 5.1 eng | 40+ pgs subs | Pass 1 strips subs, removes DTS, creates EAC3+AAC; pass 2 removes AC3 | pass (post-fix) |
| 7c | mkv | hevc | eac3 5.1 eng | none | Keep EAC3, create AAC; pass 2 (representative of Netflix-style many-subtitle files) | pass |

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
| 9c | mp4 | hevc (hev1) | aac 2.0 eng | spherical metadata | VR retag shortcut: stream-copy remux with hvc1 + faststart (no NVENC) | pass |
| 9d | mkv | hevc (hev1) | aac 2.0 eng | spherical metadata | Full VR pipeline (not MP4 — retag shortcut requires MP4) | pass |
| 9e | mp4 | h264 | aac 2.0 eng | spherical metadata | Full VR pipeline (not HEVC — retag shortcut requires HEVC) | pass |
| 9f | mp4 | hevc (hev1) | dts 5.1 eng | spherical metadata | Full VR pipeline (unwanted DTS audio — retag shortcut requires clean audio) | pass |

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

### 11. MP4-incompatible audio codecs (mux-incompatible on first pass)

| # | Container | Video | Audio | Other | Expected Outcome | Status |
|---|-----------|-------|-------|-------|-----------------|--------|
| 11a | wmv | wmv2 | wmav2 2.0 (no lang) | none | Transcode to HEVC/MP4, create AAC 2.0 from WMA in pass 1 | pass |
| 11b | mkv | hevc | adpcm_ima_wav 2.0 | none | Remux to MP4, create AAC 2.0 from ADPCM in pass 1 | pass |
| 11c | mkv | hevc | wmav2 2.0 hun + ac3 5.1 eng | none | Has safe audio — normal path, WMA stripped | pass |
| 11d | mkv | hevc | ac3 5.1 eng | none | No mux-incompatible audio — normal path | pass |
| 11e | wmv | vc1 | wmapro 5.1 + wmav2 2.0 (no lang) | none | EAC3 5.1 via fallback in pass 1, AAC 2.0 in pass 2, WMA stripped | pass |
| 11f | mkv | hevc | opus 5.1 eng | none | EAC3 5.1 in pass 1, AAC 2.0 in pass 2, Opus stripped | pass |
| 11g | mkv | av1 | opus 7.1 eng | none | Routes via 8ch gate; EAC3 + AAC, Opus stripped | pass |
| 11h | wmv | wmv3 | wmav2 mono | none | Transcode to HEVC/MP4, create AAC from the mono source in pass 1 (stays mono — the plugin clamps to the source channel count) | pass |
| 11i | mkv | hevc | vorbis 2.0 eng | none | Remux to MP4, create AAC 2.0 from Vorbis in pass 1, Vorbis stripped | pass |
| 11j | mkv | hevc | opus 2.0 pol | none | Manual review — EnsureAudioStream cannot match `pol` | pass |
| 11k | mkv | hevc | opus 5.1 jpn | none | Manual review — EAC3 has the same language limitation | pass |
| 11l | wmv | wmv3 | wmav2 2.0 untagged | none | Converts — undefined language is matched | pass |
| 11m | mkv | hevc | vorbis 2.0 eng + opus 2.0 pol | none | Converts via the eng stream | pass |

**Pass 1 audio encoder invariant:** any single FFmpeg pass may contain at most
one `ffmpegCommandEnsureAudioStream` node. The plugin emits `-ac <n>` with no
stream specifier, so FFmpeg applies it to every audio output stream and the last
node silently wins. `grd_mux_ch6` / `grd_mux_ch8` therefore mirror
`grd_eac3_ch` / `grd_eac3_ch8` exactly: when the EAC3 section will fire, the
pass-1 AAC node must not. This is enforced by the "Pass 1 audio encoder
invariant" tests in `permutation-matrix.test.mjs`.

## Known Limitations

### Non-English/non-undefined audio language (permutations 6e, 6f)

**Root cause**: `ffmpegCommandEnsureAudioStream` uses a `language` input parameter. Tdarr's `loadDefaultValues`
function silently replaces `language: ""` (intended as "any language") with the default value `"en"`. The plugin's
built-in fallback then tries `"und"`. Neither matches language tags like `"swe"`, `"cze"`, `"fre"`.

**Affected files**: Any file where ALL audio streams have a non-English, non-undefined language tag (e.g. Swedish
DTS, Czech AC3). Files with at least one "eng" or "und" stream are not affected.

**Impact**: for files that retain a safe audio track, the flow correctly fails these (Transcode error) rather
than producing silent/broken output, because the post-pass-2 audio guard catches missing audio.

For files whose ONLY audio is mux-incompatible (wma/vorbis/opus/adpcm), `grd_mux_lang_ok` /
`grd_mux_lang_foreign` divert foreign-tagged files to `fl_manual_review` before they enter the pipeline, so they
are left untouched rather than stripped of their only audio. `grd_mux_lang_foreign` holds a denylist of ISO
639-2 language codes; a tag outside that set falls through and would fail as described above. Note that a
single foreign-tagged stream is enough to divert a file: the check matches if ANY audio stream carries a
denylisted tag, so a file mixing a foreign-tagged stream with an untagged one is diverted even though the
untagged stream would have been usable.

`grd_mux_lang_foreign` is only reached once `grd_mux_lang_ok` has already found no `eng`/`und` stream, so a
file mixing `eng` with a foreign tag is never diverted — it takes the normal path and the AAC track is built
from the `eng` stream (permutation 11m). The any-stream caveat above therefore applies only to files that have
no `eng`/`und` stream at all.

**Workaround**: Re-tag the audio language to "und" via `ffmpeg -c copy -metadata:s:a:0 language=und`, then requeue.
The flow handles "und" audio correctly.

**Potential fix**: A Tdarr plugin update to support `language: "*"` (match any) or a special sentinel value that
bypasses `loadDefaultValues`. Until then, this is a known limitation of the community `EnsureAudioStream` plugin.

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

**Fix**: Replace single `cmd_rm_ac3mp3` node with two separate nodes:
- `cmd_rm_ac3` (`propertyToCheck: "codec_tag_string"`, `valuesToRemove: "ac-3"`, `condition: "includes"`) — MP4 tag "ac-3" does not substring-match EAC3 tag "ec-3"
- `cmd_rm_mp3` (`valuesToRemove: "mp3"`, `condition: "includes"`) — no ambiguity
Chain: `ffs_002 → cmd_reorder_002 → cmd_rm_ac3 → cmd_rm_mp3 → cmd_faststart2 → ffe_002`

Note: `condition: "equals"` does NOT exist in `ffmpegCommandRemoveStreamByProperty` (Tdarr v2.62.01).
Unknown conditions fall through to `not_includes` behavior, which inverts the removal logic.

### PR #66 regression (fixed in PR #67)

**Root cause**: `cmd_rm_ac3` and `cmd_rm_mp3` in pass 2 used `condition: "equals"` with
`ffmpegCommandRemoveStreamByProperty`. This plugin only recognizes `condition: "includes"` — any other
value falls through to `not_includes` logic, which **inverts** removal: removes streams that DON'T
match and keeps streams that DO. Job report confirmed: HEVC+AAC removed, AC3 kept.

**Affected permutations**: All files reaching pass 2 with AC3 or MP3 (2a, 2b, 2g, 2h, 3a, 3c, 4a, 4b,
5b, 6a, 6c, 7a, 7b, 10d, 10e, 10f, 10i).

**Fix**: Switch `cmd_rm_ac3` to `propertyToCheck: "codec_tag_string"`, `valuesToRemove: "ac-3"`,
`condition: "includes"`. In MP4 containers, AC3 tag is "ac-3" and EAC3 tag is "ec-3" — no substring
overlap. Switch `cmd_rm_mp3` to `condition: "includes"` (no ambiguity for "mp3").

### Many-stream stderr overflow (fixed in PR #70)

**Root cause**: FFmpeg demuxes ALL input streams regardless of `-map` flags. Files with 28+ subtitle
streams generate massive stderr output that overwhelms Tdarr's worker job report queue (992K+ dropped
requests observed). This destabilizes the worker and produces corrupt output. The pass 2 health check
(`chk_health_002`) also had no failure edge, so corrupt pass 1 output was silently accepted.

**Affected permutations**: 7a, 7b, 7c — any file with many subtitle/attachment streams (common in
Netflix content with 30+ subtitle languages).

**Fix**: Add `ffmpegCommandCustomArguments` nodes (`cmd_loglevel`, `cmd_vr_loglevel`) with
`-loglevel warning -stats -nostdin` as input arguments to both normal and VR FFmpeg pipelines. This suppresses
verbose demux/mux progress output that floods stderr while `-stats` re-enables the progress line for the Tdarr UI. Also add `fail_health2` (failFlow) as target for
`chk_health_002` handle "2" — corrupt pass 1 output now correctly fails the flow instead of silently
passing through.
