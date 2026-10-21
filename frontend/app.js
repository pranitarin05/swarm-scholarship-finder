/* ScholarSwarm frontend: upload -> SSE timeline -> results -> SOP editor */
const AGENTS = [
  ["supervisor", "Supervisor"],
  ["analyst", "Profile Analyst"],
  ["enricher", "Profile Enricher"],
  ["scout", "Opportunity Scout"],
  ["verifier", "Eligibility Verifier"],
  ["ranker", "Matcher / Ranker"],
  ["writer", "Application Writer"],
  ["critic", "Fact-Checker"],
];

let currentRunId = null;
let lastResult = null;
let eventSource = null;

const $ = (id) => document.getElementById(id);

function renderAgentCards() {
  $("agentCards").innerHTML = AGENTS.map(
    ([key, label]) => `
    <div id="card-${key}" class="agent-card idle bg-slate-800 border border-slate-700 rounded-lg p-3 text-center">
      <div class="text-[11px] font-semibold">${label}</div>
      <div id="status-${key}" class="text-[10px] text-slate-400 mt-1">idle</div>
    </div>`
  ).join("");
}

function setAgentStatus(agent, status) {
  const card = $("card-" + agent);
  const label = $("status-" + agent);
  if (!card || !label) return;
  card.className = "agent-card " + status + " bg-slate-800 border border-slate-700 rounded-lg p-3 text-center";
  label.textContent = status;
}

function addEventLine(evt) {
  const log = $("eventLog");
  const line = document.createElement("div");
  line.className = "event-line " + (evt.type || "");
  const time = (evt.ts || "").slice(11, 19);
  line.textContent = `[${time}] ${evt.agent}: ${evt.message || evt.type}`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function handleEvent(evt) {
  if (!evt || !evt.agent) return;
  addEventLine(evt);
  if (evt.type === "start" || evt.type === "tool_call") setAgentStatus(evt.agent, "working");
  if (evt.type === "retry" || evt.type === "fallback") setAgentStatus(evt.agent, "retrying");
  if (evt.type === "done") setAgentStatus(evt.agent, "done");
  if (evt.type === "error") setAgentStatus(evt.agent, "failed");
  if (evt.type === "handoff") setAgentStatus(evt.agent, "done");
}

function streamEvents(runId) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/runs/${runId}/events`);
  eventSource.onmessage = (msg) => {
    const evt = JSON.parse(msg.data);
    if (evt.type === "stream_end") {
      eventSource.close();
      fetchResult(runId);
      return;
    }
    handleEvent(evt);
  };
  eventSource.onerror = () => {
    eventSource.close();
    fetchResult(runId);
  };
}

async function fetchResult(runId) {
  $("runStatus").textContent = "Fetching results...";
  try {
    const resp = await fetch(`/api/runs/${runId}/result`);
    const data = await resp.json();
    if (data.status && !data.profile) {
      $("runStatus").textContent = `Status: ${data.status}`;
      setTimeout(() => fetchResult(runId), 2500);
      return;
    }
    lastResult = data;
    renderResults(data);
    $("runStatus").textContent = "Done.";
  } catch (err) {
    $("runStatus").textContent = "Failed to fetch result: " + err;
  }
}

function fieldHtml(label, field) {
  if (!field || !field.value) {
    return `<div><span class="text-slate-500">${label}:</span> <span class="badge-unverified px-1.5 py-0.5 rounded text-[10px]">not stated</span></div>`;
  }
  const verified = field.confidence && field.confidence !== "unverified" && field.source_quote;
  const badge = verified
    ? `<span class="badge-verified px-1.5 py-0.5 rounded text-[10px]">verified</span>`
    : `<span class="badge-unverified px-1.5 py-0.5 rounded text-[10px]">unverified</span>`;
  const src = field.source_url
    ? ` <a class="text-blue-400 underline text-[11px]" href="${field.source_url}" target="_blank" rel="noopener">source</a>`
    : "";
  return `<div><span class="text-slate-500">${label}:</span> ${escapeHtml(field.value)} ${badge}${src}</div>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderResults(data) {
  const profile = data.profile || {};
  $("profileSection").classList.remove("hidden");
  $("profileCard").innerHTML = `
    <div><b>${escapeHtml(profile.name || "Unknown")}</b> — ${escapeHtml(profile.degree || "")} @ ${escapeHtml(profile.institution || "")}</div>
    <div>CGPA: ${escapeHtml(profile.cgpa_raw || "?")} (${profile.cgpa_pct != null ? profile.cgpa_pct + "%" : "n/a"})
      | Skills: ${escapeHtml((profile.skills || []).slice(0, 8).join(", "))}</div>
    <div class="mt-2 text-amber-300 text-xs">Missing: ${escapeHtml((profile.missing_fields || []).join("; ") || "none")}</div>`;

  const candidates = data.candidates || [];
  if (candidates.length) {
    $("candidatesSection").classList.remove("hidden");
    $("candidateCount").textContent = `(${candidates.length})`;
    const reportById = Object.fromEntries((data.reports || []).map((r) => [r.candidate_id, r]));
    $("candidatesBody").innerHTML = candidates
      .map((c) => {
        const rep = reportById[c.id] || {};
        const st = rep.status || "unverified";
        const color = st === "verified" ? "text-green-400" : st === "partial" ? "text-amber-400" : "text-slate-500";
        return `<tr class="border-b border-slate-800/60">
          <td class="py-2 pr-4"><a class="text-blue-400 hover:underline" href="${c.url}" target="_blank" rel="noopener">${escapeHtml(c.name.slice(0, 72))}</a></td>
          <td class="pr-4">${escapeHtml(c.country || "-")}</td>
          <td class="pr-4 ${color}">${st}</td>
          <td class="text-xs text-slate-500">${c.official_url ? "official" : "discovery only"}</td>
        </tr>`;
      })
      .join("");
  }

  const top5 = data.ranked || [];
  if (top5.length) {
    $("top5Section").classList.remove("hidden");
    const candById = Object.fromEntries(candidates.map((c) => [c.id, c]));
    const repById = Object.fromEntries((data.reports || []).map((r) => [r.candidate_id, r]));
    $("top5Cards").innerHTML = top5
      .map((r, i) => {
        const c = candById[r.candidate_id] || { name: r.candidate_name, url: "", country: "" };
        const rep = repById[r.candidate_id] || {};
        const bd = r.breakdown || {};
        const bars = ["fit", "funding", "deadline", "confidence", "risk"]
          .map((k) => `<div class="flex items-center gap-2 text-[11px]"><span class="w-16 text-slate-400">${k}</span>
            <div class="flex-1 bg-slate-800 rounded"><div class="score-bar" style="width:${(bd[k] || 0)}%"></div></div>
            <span class="w-8 text-right">${bd[k] ?? 0}</span></div>`)
          .join("");
        return `<div class="bg-slate-900 border border-slate-700 rounded-xl p-5">
          <div class="flex justify-between items-start">
            <div>
              <span class="text-xs text-slate-500">#${i + 1}</span>
              <h3 class="font-semibold">${escapeHtml(c.name)}</h3>
              <a class="text-blue-400 text-xs underline" href="${c.url || rep.official_url || "#"}" target="_blank" rel="noopener">official page</a>
            </div>
            <div class="text-right">
              <div class="text-2xl font-bold text-blue-400">${r.total_score}</div>
              <div class="text-[10px] text-slate-500">/ 100</div>
            </div>
          </div>
          <p class="text-sm text-slate-300 mt-3">${escapeHtml(r.why_fits || "")}</p>
          <div class="mt-3 grid gap-1">${bars}</div>
          <div class="mt-3 text-xs space-y-1 text-slate-300">
            ${fieldHtml("Funding", rep.funding)}
            ${fieldHtml("Deadline", rep.deadline)}
            ${fieldHtml("Min grade", rep.min_grade)}
            ${fieldHtml("Degree req", rep.degree_req)}
            ${fieldHtml("English req", rep.english_req)}
            ${fieldHtml("How to apply", rep.how_to_apply)}
          </div>
          ${(rep.unmet || []).length ? `<div class="mt-2 text-xs text-red-400">Gaps: ${escapeHtml(rep.unmet.join("; "))}</div>` : ""}
          ${(rep.unknown || []).length ? `<div class="mt-1 text-xs text-amber-400/80">Unknown: ${escapeHtml(rep.unknown.slice(0, 4).join("; "))}</div>` : ""}
        </div>`;
      })
      .join("");
  }

  if (data.sop) {
    $("sopSection").classList.remove("hidden");
    $("sopEditor").value = data.sop.sop_markdown || "";
    $("evidenceMap").innerHTML =
      "<b>Evidence map:</b><ul class='mt-1 space-y-0.5'>" +
      (data.sop.evidence_map || [])
        .map((e) => `<li>• ${escapeHtml(e.claim)} — <a class="text-blue-400" href="${e.source_url}" target="_blank" rel="noopener">${escapeHtml(e.source_url)}</a></li>`)
        .join("") +
      "</ul>";
    $("checklist").innerHTML =
      "<b class='text-sm'>Document checklist:</b><ul class='mt-1 text-xs space-y-1'>" +
      (data.sop.checklist || [])
        .map((i) => `<li>${i.status === "missing" ? "❌" : "✅"} ${escapeHtml(i.item)} — ${escapeHtml(i.note || i.status)}</li>`)
        .join("") +
      "</ul>";
  }
}

$("runBtn").addEventListener("click", async () => {
  const resume = $("resume").files[0];
  if (!resume) {
    $("runStatus").textContent = "Please upload a resume PDF first.";
    return;
  }
  const form = new FormData();
  form.append("resume", resume);
  if ($("marksheet").files[0]) form.append("marksheet", $("marksheet").files[0]);
  const prefs = {
    countries: $("countries").value.split(",").map((s) => s.trim()).filter(Boolean),
    level: $("level").value,
    budget: $("budget").value,
    github_url: $("github").value.trim(),
    extra_links: $("extraLinks").value.split(",").map((s) => s.trim()).filter(Boolean),
  };
  form.append("preferences", JSON.stringify(prefs));
  form.append("demo_mode", $("demoMode").checked ? "true" : "false");
  form.append("inject_failure", $("injectFailure").value);

  $("runBtn").disabled = true;
  $("runStatus").textContent = "Starting agents...";
  $("timelineSection").classList.remove("hidden");
  $("eventLog").innerHTML = "";
  renderAgentCards();

  try {
    const resp = await fetch("/api/runs", { method: "POST", body: form });
    const data = await resp.json();
    if (!data.run_id) throw new Error(JSON.stringify(data));
    currentRunId = data.run_id;
    $("runStatus").textContent = `Run ${currentRunId}${data.demo_mode ? " (demo replay)" : ""}`;
    streamEvents(currentRunId);
  } catch (err) {
    $("runStatus").textContent = "Failed to start: " + err;
    $("runBtn").disabled = false;
  }
});

$("downloadSop").addEventListener("click", () => {
  const blob = new Blob([$("sopEditor").value], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "SOP_draft.md";
  a.click();
});

$("downloadReport").addEventListener("click", async () => {
  if (!currentRunId) return;
  window.open(`/api/runs/${currentRunId}/export`, "_blank");
});

renderAgentCards();
