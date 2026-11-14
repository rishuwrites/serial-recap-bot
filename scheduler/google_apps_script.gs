/**
 * 100% FREE External Cron & Log-Inspecting Watchdog for Serial Recap Bot (Google Apps Script)
 *
 * WHY USE THIS INSTEAD OF GITHUB CRON:
 * GitHub Actions `schedule` cron frequently delays jobs by 30-90 minutes or drops runs under load.
 * Google Apps Script runs on Google's infrastructure in your exact timezone (`Asia/Kolkata` IST)
 * for 100% free and triggers GitHub Actions via REST API at 12:00 AM IST sharp.
 *
 * HOW TO SET UP (Takes 2 minutes, 100% Free):
 * 1. Go to https://script.google.com -> click "New Project".
 * 2. Paste this entire file into `Code.gs`.
 * 3. Fill in `GITHUB_OWNER`, `GITHUB_REPO`, and `GITHUB_PAT` (a GitHub Personal Access Token with `repo` and `workflow` scope).
 * 4. In the top toolbar, select `setupTriggers` from the function dropdown and click "Run" once.
 *    - It will automatically create:
 *      a) A 12:00 AM IST midnight trigger (`triggerNightly12AM`) to start all 9 shows.
 *      b) A 2-hour watchdog trigger (`watchdogCheckAndRerun`) that reads back GitHub workflow run status & job logs
 *         and automatically re-runs any failed show job.
 */

const GITHUB_OWNER = "YOUR_GITHUB_USERNAME";
const GITHUB_REPO  = "serial-recap-bot";
const GITHUB_PAT   = "YOUR_GITHUB_PAT_TOKEN";
const WORKFLOW_FILE = "recap.yml";
const BRANCH        = "main";
const SHOW_KEYS     = ["kaisi", "bhabhi", "sohni", "rimjhim", "mannsundar", "atisundar", "patibrahma", "ishq", "saanche"];

/**
 * Run this function ONCE in Google Apps Script editor to install the exact 12:00 AM IST trigger
 * plus the automatic log-checking watchdog trigger.
 */
function setupTriggers() {
  // Remove any old duplicate triggers first
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));

  // 1. Exact 12:00 AM IST (Midnight) Nightly Trigger
  ScriptApp.newTrigger("triggerNightly12AM")
    .timeBased()
    .atHour(0)
    .nearMinute(1)
    .everyDays(1)
    .inTimezone("Asia/Kolkata")
    .create();

  // 2. Watchdog Log-Inspector & Auto-Rerunner (checks every 2 hours to catch & rerun any failed job)
  ScriptApp.newTrigger("watchdogCheckAndRerun")
    .timeBased()
    .everyHours(2)
    .create();

  Logger.log("Installed 12:00 AM IST nightly trigger + 2-hour watchdog log checker!");
}

/**
 * Fires at 12:00 AM IST every night to generate & schedule all 9 videos, including Saanche at 12:30 PM IST.
 */
function triggerNightly12AM() {
  dispatchWorkflow("all");
}

/**
 * Inspects recent GitHub Actions runs & job logs:
 * - If no run started today after 12:00 AM IST, triggers the full nightly run.
 * - If any individual show job failed, timed out, or was cancelled, reads its log and re-triggers ONLY that show.
 */
function watchdogCheckAndRerun() {
  const nowIst = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const hourIst = nowIst.getHours();
  // Run active recovery checks between 2:00 AM IST and 11:00 AM IST
  if (hourIst < 2 || hourIst > 11) {
    Logger.log("Outside recovery window (02:00 - 11:00 IST); skipping.");
    return;
  }

  const runsUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/runs?per_page=10`;
  const resp = UrlFetchApp.fetch(runsUrl, { headers: ghHeaders(), muteHttpExceptions: true });
  if (resp.getResponseCode() !== 200) {
    Logger.log("Failed to fetch runs: " + resp.getContentText());
    return;
  }

  const runs = JSON.parse(resp.getContentText()).workflow_runs || [];
  const todayStr = Utilities.formatDate(new Date(), "Asia/Kolkata", "yyyy-MM-dd");

  // Check if any run is currently in_progress or queued
  const activeRun = runs.find(r => r.status === "in_progress" || r.status === "queued");
  if (activeRun) {
    Logger.log("A workflow run is currently active (ID: " + activeRun.id + "); waiting for it to finish.");
    return;
  }

  // Filter runs from today (IST)
  const todayRuns = runs.filter(r => {
    const runDateIst = Utilities.formatDate(new Date(r.created_at), "Asia/Kolkata", "yyyy-MM-dd");
    return runDateIst === todayStr;
  });

  if (todayRuns.length === 0) {
    Logger.log("No workflow run found for today (" + todayStr + ")! Triggering full nightly run now...");
    dispatchWorkflow("all");
    return;
  }

  // Inspect jobs of the latest run today to see if any show failed
  const latestRun = todayRuns[0];
  const jobsUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${latestRun.id}/jobs?per_page=50`;
  const jobsResp = UrlFetchApp.fetch(jobsUrl, { headers: ghHeaders(), muteHttpExceptions: true });
  if (jobsResp.getResponseCode() === 200) {
    const jobs = JSON.parse(jobsResp.getContentText()).jobs || [];
    for (const job of jobs) {
      const name = (job.name || "").toLowerCase();
      const conclusion = (job.conclusion || "").toLowerCase();
      if (conclusion === "failure" || conclusion === "timed_out" || conclusion === "cancelled") {
        for (const showKey of SHOW_KEYS) {
          if (name.includes(showKey)) {
            Logger.log(`Detected failed job for show '${showKey}' (${conclusion}). Dispatched targeted rerun!`);
            dispatchWorkflow(showKey);
            Utilities.sleep(2000);
          }
        }
      }
    }
  }

}

function dispatchWorkflow(onlyShow) {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
  const payload = {
    ref: BRANCH,
    inputs: {
      only_show: onlyShow,
      video_id: "",
      test_mode: "false",
      upload: "true",
      publish_now: "false"
    }
  };
  const res = UrlFetchApp.fetch(url, {
    method: "post",
    headers: ghHeaders(),
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  Logger.log(`Dispatched workflow (only_show=${onlyShow}): HTTP ${res.getResponseCode()}`);
}

function ghHeaders() {
  return {
    "Authorization": "Bearer " + GITHUB_PAT,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
  };
}
