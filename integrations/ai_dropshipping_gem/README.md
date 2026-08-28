# AI Dropshipping Gem integration

This integration treats CapCut Mate as the only production edit/render path.
FFmpeg may be used for analysis, preprocessing, and preview QA, but never as the
production renderer.

Production flow:

1. Resolve rights-cleared public Reel materials into an asset manifest.
2. Detect burned-in foreign captions with `subtitle_detector.py`.
3. Use inpainting when the confidence gate passes; otherwise add a white-label
   cover layer and Japanese caption track.
4. Build `HOOK -> DEMO -> REACTION/PROOF -> BENEFIT -> CTA` recipes.
5. Add clips, captions, SFX/BGM tracks, transitions, masks, and keyframes.
6. Submit the draft to a Windows node through `gen_video`.
7. Poll `gen_video_status`, download `video_url`, run QA, and revise/re-render.

Environment:

```text
CAPCUT_MATE_BASE_URL=http://windows-render-node:30000
CAPCUT_MATE_API_KEY=
ENABLE_APIKEY=false
STORAGE_BACKEND=local
SELF_HOST_BASE_URL=https://capcut-mate.example.com
```

The production path does not require jcaigc.cn. Run this fork on the Windows
render node with Jianying installed and signed in. The self-hosted API creates
the timeline, `gen_video` drives Jianying, and `gen_video_status.video_url`
points to this instance's `/files/rendered/` path.

All acquired materials default to `rights_status=pending`. A rendered file is
not eligible for publishing until both rights clearance and human approval are
recorded.
