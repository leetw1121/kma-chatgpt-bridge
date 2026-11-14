/**
 * INDEPENDENT BACKUP SCHEDULER — Cloudflare Worker Cron
 *
 * Does not collect or publish weather. It checks whether the latest KMA issue
 * has a matching public, complete PDF. If missing/stale, it uses GitHub's
 * workflow_dispatch API to run the EXISTING verified collector.
 *
 * Never put the GitHub token in this file. Set secret GITHUB_ACTIONS_TOKEN.
 * Cron in UTC: 12 3,9,15,21 * * * => KST 00:12/06:12/12:12/18:12.
 */
export const OWNER = "leetw1121";
export const REPO = "kma-chatgpt-bridge";
export const WORKFLOW = "update_kma.yml";
const TZ_OFFSET_MS = 9 * 60 * 60 * 1000;
const ISSUE_HOURS = [23, 20, 17, 14, 11, 8, 5, 2];
const RELEASE_GRACE_MS = 18 * 60 * 1000;
const MAX_DATA_AGE_MS = 4 * 60 * 60 * 1000;

export function expectedIssueUTC(nowMs) {
  const cutoff = nowMs - RELEASE_GRACE_MS;
  const localCutoff = new Date(cutoff + TZ_OFFSET_MS);
  const yyyy = localCutoff.getUTCFullYear();
  const mm = localCutoff.getUTCMonth();
  const dd = localCutoff.getUTCDate();
  for (const dayBack of [0, 1, 2]) {
    for (const hour of ISSUE_HOURS) {
      const utc = Date.UTC(yyyy, mm, dd - dayBack, hour) - TZ_OFFSET_MS;
      if (utc <= cutoff) return utc;
    }
  }
  throw new Error("Cannot compute current KMA release");
}

export function isFresh(publicForecast, manifest, nowMs) {
  if (!publicForecast || !manifest) return { ok: false, reason: "missing_result" };
  if (manifest.scope !== "city_only") return { ok: false, reason: "wrong_pdf_scope" };
  if (manifest.source_generated_at_kst !== publicForecast.generated_at_kst) {
    return { ok: false, reason: "pdf_not_matching_forecast" };
  }
  if (manifest.source_issue_at_kst !== publicForecast.issue_at_kst) {
    return { ok: false, reason: "pdf_issue_mismatch" };
  }
  const published = Date.parse(publicForecast.generated_at_kst);
  const issue = Date.parse(publicForecast.issue_at_kst);
  if (!Number.isFinite(published) || !Number.isFinite(issue)) {
    return { ok: false, reason: "invalid_timestamp" };
  }
  if (nowMs - published < -5 * 60_000 || nowMs - published > MAX_DATA_AGE_MS) {
    return { ok: false, reason: "stale_or_future_data" };
  }
  if (issue < expectedIssueUTC(nowMs)) return { ok: false, reason: "new_kma_issue_missing" };
  const attempted = Number(publicForecast.locations_checked);
  const succeeded = Number(publicForecast.locations_succeeded);
  if (!Number.isInteger(attempted) || attempted < 100 || succeeded !== attempted) {
    return { ok: false, reason: "incomplete_kma_coverage" };
  }
  if (Number(manifest.pdf_size_bytes) < 1000) {
    return { ok: false, reason: "invalid_pdf_size" };
  }
  return { ok: true, reason: "already_current" };
}

async function fetchJson(path) {
  const url = `https://raw.githubusercontent.com/${OWNER}/${REPO}/main/data/${path}`;
  const response = await fetch(url, {
    method: "GET",
    headers: { "Accept": "application/json", "Cache-Control": "no-cache" },
    cf: { cacheTtl: 0 },
  });
  if (!response.ok) throw new Error(`Unable to fetch ${path}: HTTP ${response.status}`);
  return response.json();
}

async function maybeInProgress(token, nowMs) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/runs?status=in_progress&per_page=5`;
  try {
    const response = await fetch(url, { headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "kma-forecast-external-watchdog",
    }, cf: { cacheTtl: 0 } });
    if (!response.ok) return false;
    const data = await response.json();
    return (data.workflow_runs || []).some((x) => {
      const start = Date.parse(x.run_started_at || x.created_at);
      return Number.isFinite(start) && start <= nowMs && nowMs - start < 7 * 60_000;
    });
  } catch {
    // Status lookup is advisory; do not prevent the fallback on network errors.
    return false;
  }
}

async function dispatch(token) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "kma-forecast-external-watchdog",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (response.status !== 204) {
    throw new Error(`GitHub workflow_dispatch failed HTTP ${response.status}`);
  }
}

export async function checkAndDispatch(env, nowMs = Date.now()) {
  if (!env.GITHUB_ACTIONS_TOKEN) throw new Error("Cloudflare Worker secret missing: GITHUB_ACTIONS_TOKEN");
  let result;
  try {
    const [forecast, manifest] = await Promise.all([
      fetchJson("latest.json"), fetchJson("pdf_manifest.json"),
    ]);
    result = isFresh(forecast, manifest, nowMs);
  } catch (error) {
    // Fail open to the existing collector; never pretend the PDF was checked.
    result = { ok: false, reason: "unable_to_validate_public_feed" };
    console.warn("Source check failed:", String(error).slice(0, 250));
  }
  if (result.ok) {
    console.log("No dispatch; PDF already fresh", JSON.stringify(result));
    return "already_current";
  }
  if (await maybeInProgress(env.GITHUB_ACTIONS_TOKEN, nowMs)) {
    console.log("Existing KMA job is active; avoiding duplicate", result.reason);
    return "collector_in_progress";
  }
  await dispatch(env.GITHUB_ACTIONS_TOKEN);
  console.log("Backup GitHub workflow dispatch sent:", result.reason);
  return "dispatched";
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(checkAndDispatch(env, controller.scheduledTime));
  },
};
