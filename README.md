# Serial recap bot (Dangal TV serials -> 2-3 min "motion comic" recaps), standalone repo

## Daily Publish Schedule (9:00 AM – 4:00 PM IST)
The bot runs at **12:00 AM IST (Midnight)** via an external cron trigger and schedules the 8 daily recap videos on YouTube at hourly slots from **9:00 AM to 4:00 PM IST**:

| Show Key | Serial Name | Scheduled YouTube Publish Time (IST) |
| :--- | :--- | :--- |
| `kaisi` | Kaisi Teri Dillagi (कैसी तेरी दिल्लगी) | **09:00 AM IST** |
| `bhabhi` | Hamari Bhabhi Number 1 (हमारी भाभी नंबर 1) | **10:00 AM IST** |
| `sohni` | Sohni Aur Nahi Koi Honi (सोहनी और नहीं कोई होनी) | **11:00 AM IST** |
| `rimjhim` | Rimjhim Choti Umar Bada Safar (रिमझिम छोटी उम्र बड़ा सफर) | **12:00 PM IST** |
| `mannsundar` | Mann Sundar (मन सुंदर) | **01:00 PM IST** |
| `atisundar` | Mann Atisundar (मन अतिसुंदर) | **02:00 PM IST** |
| `patibrahma` | Pati Brahmachari (पति ब्रह्मचारी) | **03:00 PM IST** |
| `ishq` | Ishq Junooni (इश्क़ जुनूनी) | **04:00 PM IST** |

Each uploaded video gets a generated description with the show name, source and target episode/date, show-specific Hindi/English search phrases and hashtags, the recap/prediction, and the official source episode link. Search terms are generated for the next-day (+1) and two-day-ahead (+2) episode/date only, not for the source day. The description identifies the upload as a recap, not as the full episode.

Example description for [Mann Atisundar episode 1193](./examples/mann_atisundar_MZLmmpAv87Q_description.txt).

## Why We Don't Use GitHub's Built-In Cron (And How to Trigger at 12:00 AM IST Sharp for Free)
GitHub's internal `schedule:` cron frequently delays runs by 30–90 minutes or drops them during peak hours. Instead, this repo uses **external `workflow_dispatch` / `repository_dispatch`** triggered at **12:00 AM IST sharp** using any of the 100% free external schedulers included in `/scheduler`:

1. **Option A — Google Apps Script (`scheduler/google_apps_script.gs`, Recommended & 100% Free)**:
   - Open [script.google.com](https://script.google.com), create a project, and paste `scheduler/google_apps_script.gs`.
   - Set `GITHUB_OWNER`, `GITHUB_REPO`, and `GITHUB_PAT` (a GitHub Personal Access Token with `repo` and `workflow` permissions).
   - Run `setupTriggers()` once. It installs an exact **12:00 AM IST (`Asia/Kolkata`)** daily trigger plus a **2-hour Watchdog Log-Inspector** that checks GitHub run logs and automatically re-triggers any failed show.
2. **Option B — Cloudflare Worker (`scheduler/cloudflare_worker.js`, 100% Free)**:
   - Deploy `scheduler/cloudflare_worker.js` on Cloudflare Workers Free Tier with cron `30 18 * * *` (12:00 AM IST) and `30 20,22,0,2 * * *` (watchdog checks).
3. **Option C — cron-job.org (100% Free Web Cron)**:
   - Create a free job at [cron-job.org](https://cron-job.org) set to **00:00 Asia/Kolkata (12:00 AM IST)**.
   - Method: `POST` to `https://api.github.com/repos/<OWNER>/<REPO>/actions/workflows/recap.yml/dispatches`
   - Headers: `Authorization: Bearer <YOUR_GITHUB_PAT>`, `Accept: application/vnd.github+json`
   - Body: `{"ref":"main","inputs":{"only_show":"all","test_mode":"false","upload":"true","publish_now":"false"}}`

## 3-Layer Self-Healing Log Watchdog & Zero-Duplicate Protection
1. **In-Job Log Capture & Auto-Retry (`recap.yml` & `main.py`)**:
   - Every show job streams its logs into `state/<show>_status.json`. If Attempt 1 fails (e.g., temporary Gemini 503 or network timeout), the job reads the status log, cleans up temp files, waits 45 seconds, and retries automatically inside the same run.
2. **Post-Run Watchdog Supervisor (`recap/watchdog.py`)**:
   - After the matrix finishes (and whenever triggered with `only_show=watchdog`), `watchdog.py` inspects the GitHub Actions job logs, reads `state/<show>_status.json`, and checks Dangal TV's live feed. If any show failed, timed out, or has an unprocessed new episode, it automatically dispatches a targeted rerun for **only that show** (up to 4 self-healing attempts per day).
3. **Triple Anti-Duplicate Lock (Never Uploads an Episode Twice)**:
   - **Video ID + Episode Number State (`state/<show>.json`)**: Stores both the source YouTube video ID (`JNdlYnNneKE`) and the episode number (`ep:452`, `target_ep:453`). Even if Dangal TV re-uploads the same episode with a new video ID, it is never processed twice.
   - **Live YouTube Channel Pre-Check (`youtube.already_uploaded()`)**: Before generating a video AND again right before uploading, the bot queries your YouTube channel's uploads list (including private/scheduled videos). If an episode is already uploaded or scheduled on your channel, it syncs `state/<show>.json` and skips immediately.

## Facebook Pages and Instagram Reels

Social publishing is **disabled by default**. The existing YouTube schedule, OAuth secrets, episode state, landscape output (`output/<show>_<source-video-id>.mp4`), and landscape thumbnail remain in place. When enabled, Facebook receives that landscape MP4; Instagram gets a distinct 1080 × 1920 MP4 rendered from the same illustrations, narration, word-boundary timings, and music.

### Architecture and outputs

The pipeline still analyzes an episode once, synthesizes narration once, and generates illustrations once. `recap/vertical.py` builds portrait scenes from the original illustration files: the complete source illustration is contained in the foreground, with a softly blurred version behind it to fill the portrait canvas. It uses the existing Devanagari font and caption timing helpers. The additional file is `output/<show>_<source-video-id>_vertical.mp4`. Set `RENDER_VERTICAL=true` to create a local preview without enabling Instagram.

Facebook publishing in `recap/facebook.py` uses Meta's documented resumable upload session to upload a local MP4, then publishes the returned upload handle to the configured Page's `/videos` endpoint. Instagram publishing in `recap/instagram.py` uses the Facebook Login for Business resumable flow: create a `REELS` container, upload the local file to `rupload.facebook.com`, poll processing status, and publish the ready container. Neither flow needs a public video URL.

Each destination is recorded independently in `state/<show>_publishing.json`; it does not replace existing YouTube state. Successful destinations are skipped on retries. Definite failures are eligible for retry on a later workflow run. Timed-out publishing requests and abandoned `in_progress` attempts are marked unresolved and are **not** blindly retried, because the remote service may already have published the video. The workflow serializes runs to reduce concurrent duplicate work.

Meta API workflows were implemented against the official documentation:

- [Facebook Page video publishing and resumable uploads](https://developers.facebook.com/documentation/video-api/guides/publishing)
- [Instagram content publishing and Reels](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
- [Instagram Platform overview and account requirements](https://developers.facebook.com/documentation/instagram-platform/overview)

The Reel publisher validates the API's current media constraints before making requests: H.264 4:2:0 at 1080 × 1920, 23–60 FPS, AAC up to 48 kHz/128 kbps with one or two channels, 3 seconds–15 minutes, and no more than 300 MB. The renderer targets 24 FPS and 44.1 kHz AAC. The Graph API version defaults to `v26.0` (the version used in the current official examples reviewed for this integration). Set `META_GRAPH_API_VERSION` when upgrading, after verifying the current Meta docs and changelog.

### Episode-aware thumbnail artwork

For each episode, the bot asks Gemini to describe the source video's public YouTube thumbnail and uses only its broad composition and color balance as guidance. It then generates a fresh 1280 × 720 illustration with the FLUX.1-schnell Gradio Space and applies the bot's existing Hindi title, show, and episode overlays. The image prompt uses the analyzed episode hook and scene, and instructs the model not to reproduce faces, text, or logos from the source thumbnail. If thumbnail analysis or the public FLUX Space is unavailable, the bot logs the issue and falls back to the existing episode illustrations/providers.

`gradio_client` is installed from `requirements.txt`. `HF_FLUX_SPACE` optionally overrides the public Gradio Space URL (set it as a GitHub Actions repository variable for the workflow); no Hugging Face token is required for the default public Space. Gemini uses the existing `GEMINI_API_KEY`. The implementation uses normal HTTPS networking and does not change process-wide DNS behavior.

### Software and local configuration

Install Python dependencies and FFmpeg. On Ubuntu/GitHub Actions, FFmpeg and the Noto Devanagari fonts are installed by the workflow. Pillow must have RAQM shaping support for correctly positioned Devanagari marks; the workflow installs its system libraries. On Windows, install FFmpeg and make `ffmpeg` and `ffprobe` available on `PATH`; use a Pillow build with RAQM support and the renderer will look for the Windows Nirmala Devanagari font. A basic Pillow build can fall back to unshaped text, so verify captions before publishing. Use the existing Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Use `.env.example` as a list of supported environment variables. The application reads values from its process environment and does not automatically load a `.env` file; configure your terminal, IDE, or secret manager accordingly. Any local `.env` is ignored by Git. Do not store real tokens in the example file, source code, or committed state.

### Configure the Meta app and tokens

1. Create a Meta developer app for the business and add the Facebook Login for Business and Instagram Graph API products. Complete business verification and any app review/Advanced Access requirements that Meta displays for the required permissions.
2. For Facebook, use a Page whose operator has the Page `CREATE_CONTENT` task. Grant the app user `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`. Obtain the Page ID, a Page access token, the app ID, and a User access token for the Meta resumable upload session. The User token and Page token are separate values.
3. For Instagram, convert the Instagram account to a Creator or Business professional account and connect it to the appropriate Facebook Page. Use Facebook Login for Business and grant `instagram_basic`, `instagram_content_publish`, and `pages_read_engagement`; Facebook Page publishing additionally uses `pages_show_list` and `pages_manage_posts`. Some Business Manager configurations may require additional permissions shown in Meta's account-specific authorization flow. Complete Page Publishing Authorization if the Page requires it.
4. Store tokens as GitHub Actions **Secrets** (or local `.env` values), never as repository variables or committed files. For Instagram's Facebook Login flow, use the authorized Facebook user/Page token accepted by Meta's current publishing flow; `INSTAGRAM_ACCESS_TOKEN` is separate from the Facebook Page publishing token setting if you choose to store them separately. The app ID and account/Page IDs are identifiers, not access tokens. A Meta app secret is not needed by these publishing requests and is intentionally not collected or logged.
5. Follow Meta's current token-expiration and renewal guidance for the selected Facebook Login for Business flow. Renew/re-authorize tokens through Meta's supported login flow before expiry, then update the corresponding secret. If a permission is revoked, resolve it in Meta Business settings and reauthorize; never place tokens in logs or issue reports.

### Environment variables and GitHub Actions settings

| Name | GitHub storage | Purpose |
| --- | --- | --- |
| `FACEBOOK_ENABLED` | Repository variable; default `false` | Explicitly opt in to Facebook publishing |
| `FACEBOOK_PAGE_ID` | Repository variable | Destination Facebook Page |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | Secret | Publishes the video to the Page |
| `META_APP_ID` | Repository variable | Starts the Meta resumable file-upload session |
| `META_USER_ACCESS_TOKEN` | Secret | Upload-session token required by Meta |
| `FACEBOOK_DESCRIPTION` | Optional repository variable | Overrides the generated Facebook description; `{title}`, `{description}`, `{show}`, `{episode}`, and `{hook}` are supported |
| `INSTAGRAM_ENABLED` | Repository variable; default `false` | Explicitly opt in to Instagram Reels publishing |
| `INSTAGRAM_USER_ID` | Repository variable | Instagram professional-account ID |
| `INSTAGRAM_ACCESS_TOKEN` | Secret | Facebook Login for Business token authorized for this account |
| `INSTAGRAM_CAPTION` | Optional repository variable | Caption override; the API enforces a 2,200-character maximum and accepts the same placeholders |
| `META_GRAPH_API_VERSION` | Optional repository variable | Graph API version; defaults to `v26.0` |
| `RENDER_VERTICAL` | Optional repository variable; default `false` | Render a portrait preview even when Instagram publishing is disabled |

The existing YouTube secrets (`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`) and other workflow settings remain unchanged. If no Meta settings are configured, the current YouTube-only workflow runs as before.

### Safe run and preview instructions

- **YouTube-only:** leave both Meta enable flags unset or set them to `false`.
- **Local portrait preview without publishing:** set `RENDER_VERTICAL=true`, `DO_UPLOAD=false`, and provide a specific video ID, then run:

  ```powershell
  $env:SHOW_KEY = "kaisi"
  $env:VIDEO_ID = "REPLACE_WITH_A_SOURCE_VIDEO_ID"
  $env:DO_UPLOAD = "false"
  $env:RENDER_VERTICAL = "true"
  python recap/main.py
  ```

  This still uses the configured analysis, image, and voice services to render the episode, but it does not upload to YouTube, Facebook, or Instagram. Preview both MP4 files in `output/`.
- **Automated tests:** `python -m unittest discover -s tests -v`. These tests mock Meta APIs and use synthetic FFmpeg audio/video; they do not require credentials or publish content.
- To publish, set the appropriate `FACEBOOK_ENABLED=true` or `INSTAGRAM_ENABLED=true` only after credentials, permissions, account linkage, and output previews have been verified. The action's `upload` input must also be `true`.

### Partial failures and troubleshooting

Check `state/<show>_publishing.json` for each destination's status, attempt count, media ID, timestamp, and last error. A destination with `failed` status is eligible for a later retry; a `success` destination will not be repeated if another platform failed. For `uncertain` or stale `in_progress`, first inspect the Page or Instagram account and Meta's container/upload status. If it was published, record its returned media ID and mark that platform successful; if Meta confirms it was not published, reset that platform to a definite failed status before retrying. Never clear the entire publishing state just to retry one platform.

Common issues include missing Page tasks or permissions, an expired/revoked token (Meta error code 190), an Instagram account that is not professional or is not linked to the authorized Page, incomplete Page Publishing Authorization, and Graph API version changes. Fix the account/app authorization first; a successful destination remains protected from duplicate publication.
