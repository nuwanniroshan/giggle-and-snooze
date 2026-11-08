# FFmpeg from Python

- ✓ Call `subprocess.run([...], check=True, capture_output=True, text=True)` with an argument list. Never `shell=True` or string concatenation.
- ✓ Build filtergraphs in pure functions that return strings, so they can be unit-tested. For long graphs, write them to a file and pass `-filter_complex_script`.
- ✓ Quote expressions inside the graph (`x='iw/2-(iw/zoom/2)'`), and don't let Python f-strings swallow `{}` or `:`.
- ✓ For zoompan, upscale the input first (e.g. `scale=4*1920:-1`) and set `d`, `s=1920x1080` and `fps` explicitly to avoid jitter.
- ✓ xfade needs identical resolution, fps, pixel format and timebase on both inputs. Normalise with `scale,fps,format=yuv420p,settb=AVTB` first. Each `offset` = cumulative duration − transition length.
- ✓ sidechaincompress: `asplit` the voice track so one copy keys the compressor on the music and the other is mixed, then `amix` with `normalize=0`.
- ✓ For YouTube output use `-pix_fmt yuv420p -movflags +faststart` and tag BT.709 colour.
- ✓ Get durations from `ffprobe -of json`, never guess them. On failure, log FFmpeg's stderr.
- ✗ Don't drop audio by mapping only the video chain. Map `[aout]` explicitly.
- ✗ Don't chain many xfades over full-length clips in one graph on 16 GB. Render segments, then concat.
