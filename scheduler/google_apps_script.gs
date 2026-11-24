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
 * 3. Add a Script Property named `GITHUB_PAT` with a token that can dispatch workflows and read repository state.
 * 4. In the top toolbar, select `setupTriggers` from the function dropdown and click "Run" once.
 *    - It will automatically create:
 *      a) A 12:00 AM IST midnight trigger (`triggerNightly12AM`) to start all 9 shows.
 *      b) A 2-hour watchdog trigger (`watchdogCheckAndRerun`) that reads back GitHub workflow run status & job logs
 *         and automatically re-runs any failed show job.
 */

const GITHUB_OWNER = "rishuwrites";
const GITHUB_REPO  = "serial-recap-bot";
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
 * Retries only shows without a successful current-day job or current-day success state.
 */
function watchdogCheckAndRerun() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) {
    Logger.log("Another watchdog execution is active; skipping this check.");
    return;
  }
  try {
    const now = new Date();
    const hourIst = Number(Utilities.formatDate(now, "Asia/Kolkata", "H"));
    if (hourIst < 2) {
      Logger.log("Before 02:00 IST; leaving time for the nightly dispatch.");
      return;
    }

    const today = Utilities.formatDate(now, "Asia/Kolkata", "yyyy-MM-dd");
    const runsUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/runs?branch=${BRANCH}&per_page=100`;
    const runs = fetchGitHubJson(runsUrl).workflow_runs || [];
    const todayRuns = runs.filter(run =>
      Utilities.formatDate(new Date(run.created_at), "Asia/Kolkata", "yyyy-MM-dd") === today
    );
    if (runs.some(run => run.status === "queued" || run.status === "in_progress")) {
      Logger.log("A workflow run is active; waiting for it to finish.");
      return;
    }

    const latestJobByShow = {};
    for (const run of todayRuns) {
      const jobsUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${run.id}/jobs?per_page=100`;
      const jobs = fetchGitHubJson(jobsUrl).jobs || [];
      for (const job of jobs) {
        const name = (job.name || "").toLowerCase();
        const showKey = SHOW_KEYS.find(key => name.includes(key));
        if (showKey && !latestJobByShow[showKey] && job.conclusion !== "skipped") {
          latestJobByShow[showKey] = {
            conclusion: (job.conclusion || "").toLowerCase(),
            runId: run.id
          };
        }
      }
    }

    const statesByShow = {};
    for (const showKey of SHOW_KEYS) {
      statesByShow[showKey] = fetchShowStatusFromGitHub(showKey);
    }

    const failedRunIds = new Set();
    for (const showKey of SHOW_KEYS) {
      const state = statesByShow[showKey];
      const stateSuccess = state && state.date_ist === today && String(state.status || "").toLowerCase() === "success";
      const stateFailed = state && state.date_ist === today && String(state.status || "").toLowerCase() === "failed";
      const job = latestJobByShow[showKey];
      if (stateSuccess || (!stateFailed && job && job.conclusion === "success")) {
        continue;
      }
      if (job && ["failure", "timed_out", "cancelled"].includes(job.conclusion)) {
        failedRunIds.add(job.runId);
      }
    }

    if (failedRunIds.size > 0) {
      const runId = Array.from(failedRunIds).sort((a, b) => Number(b) - Number(a))[0];
      rerunFailedJobs(runId);
      return;
    }

    const hasSuccessState = SHOW_KEYS.some(showKey => {
      const state = statesByShow[showKey];
      return state && state.date_ist === today && String(state.status || "").toLowerCase() === "success";
    });
    if (todayRuns.length === 0 && !hasSuccessState) {
      Logger.log(`No successful show state or workflow run for ${today}; dispatching the nightly batch.`);
      dispatchWorkflow("all");
      return;
    }

    for (const showKey of SHOW_KEYS) {
      const state = statesByShow[showKey];
      const stateSuccess = state && state.date_ist === today && String(state.status || "").toLowerCase() === "success";
      const stateFailed = state && state.date_ist === today && String(state.status || "").toLowerCase() === "failed";
      const job = latestJobByShow[showKey];
      if (stateSuccess) {
        Logger.log(`[${showKey}] Today's state is successful; no rerun.`);
        continue;
      }
      if (!stateFailed && job && job.conclusion === "success") {
        Logger.log(`[${showKey}] Today's workflow job succeeded; no rerun.`);
        continue;
      }

      const reason = (job && job.conclusion) || (state && state.date_ist === today ? state.status : "no successful run or state");
      Logger.log(`[${showKey}] Not successful for ${today} (${reason}); dispatching only this show.`);
      dispatchWorkflow(showKey);
      return;
    }
  } finally {
    lock.releaseLock();
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
  if (res.getResponseCode() !== 204) {
    throw new Error(`Workflow dispatch failed (HTTP ${res.getResponseCode()}): ${res.getContentText()}`);
  }
  Logger.log(`Dispatched workflow (only_show=${onlyShow}).`);
}

function fetchShowStatusFromGitHub(showKey) {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/state/${showKey}_status.json?ref=${BRANCH}`;
  const res = UrlFetchApp.fetch(url, { headers: ghHeaders(), muteHttpExceptions: true });
  if (res.getResponseCode() === 404) {
    return null;
  }
  if (res.getResponseCode() !== 200) {
    throw new Error(`Could not read ${showKey} state (HTTP ${res.getResponseCode()}): ${res.getContentText()}`);
  }
  const content = (JSON.parse(res.getContentText()).content || "").replace(/\s/g, "");
  const stateText = Utilities.newBlob(Utilities.base64Decode(content)).getDataAsString("UTF-8");
  return JSON.parse(stateText);
}

function fetchGitHubJson(url) {
  const res = UrlFetchApp.fetch(url, { headers: ghHeaders(), muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) {
    throw new Error(`GitHub API request failed (HTTP ${res.getResponseCode()}): ${res.getContentText()}`);
  }
  return JSON.parse(res.getContentText());
}

function rerunFailedJobs(runId) {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${runId}/rerun-failed-jobs`;
  const res = UrlFetchApp.fetch(url, {
    method: "post",
    headers: ghHeaders(),
    muteHttpExceptions: true
  });
  if (res.getResponseCode() !== 201 && res.getResponseCode() !== 202) {
    throw new Error(`Failed to rerun failed jobs for run ${runId} (HTTP ${res.getResponseCode()}): ${res.getContentText()}`);
  }
  Logger.log(`Reran only failed jobs from workflow run ${runId}.`);
}

function ghHeaders() {
  const token = PropertiesService.getScriptProperties().getProperty("GITHUB_PAT");
  if (!token) {
    throw new Error("Missing Script Property GITHUB_PAT. Add a token with workflow and repo access.");
  }
  return {
    "Authorization": "Bearer " + token,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
  };
}
