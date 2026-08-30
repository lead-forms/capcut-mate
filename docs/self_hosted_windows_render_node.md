# Self-hosted Windows render node

This is the production render path for AI Dropshipping Gem. It does not call
jcaigc.cn and does not require its API key, storage, authentication, or billing.

## Topology

`AI Dropshipping Gem -> this API on Windows -> Jianying -> /files/rendered -> video_url`

The API and renderer intentionally run in the same Windows process because
Jianying export is controlled through Windows UI Automation. A reverse proxy or
private tunnel may expose port 30000 to the control plane.

## Required Windows software

- Windows 10/11 or Windows Server with an interactive desktop session
- Jianying Pro, installed and signed in
- Python 3.11+
- Git and uv

## Install

```powershell
git clone https://github.com/lead-forms/capcut-mate.git
cd capcut-mate
git remote add upstream https://github.com/Hommy-master/capcut-mate.git
uv sync --extra windows
```

## Environment

```powershell
$env:SELF_HOST_BASE_URL="https://capcut-mate.example.com"
$env:SERVER_HOST="127.0.0.1"
$env:SERVER_PORT="30000"
$env:DRAFT_SAVE_PATH="$env:LOCALAPPDATA\JianyingPro\User Data\Projects\com.lveditor.draft"
$env:ENABLE_APIKEY="false"
$env:STORAGE_BACKEND="local"
uv run main.py
```

Set `SELF_HOST_BASE_URL` to the private/public HTTPS address that AI
Dropshipping Gem can reach. Protect that address at the network or reverse
proxy layer; disabling the third-party billing key is not an instruction to
expose the renderer openly.

The server binds to `127.0.0.1:30000` by default. Keep this localhost-only
setting for initial rendering tests. Set `SERVER_HOST` to another interface
only after authentication and network access controls are enabled.

## Completion check

1. Create and save a draft through the API.
2. Call `POST /openapi/capcut-mate/v1/gen_video` with `draft_url` only.
3. Poll `POST /openapi/capcut-mate/v1/gen_video_status`.
4. Require `status=completed`, `progress=100`, and a non-empty `video_url`.
5. Download the URL from `/files/rendered/...` and run final MP4 QA.

No FFmpeg fallback is part of this production path.
