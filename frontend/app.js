/* ScholarSwarm Frontend: Modern LangGraph Multi-Agent Architecture */

const AGENTS = [
  { id: "supervisor", name: "Supervisor", role: "Planning & Graph Orchestration", icon: "cpu" },
  { id: "analyst", name: "Profile Analyst", role: "PyMuPDF Text & GPA Normalizer", icon: "file-text" },
  { id: "enricher", name: "Profile Enricher", role: "GitHub REST API & Code Evidence", icon: "github" },
  { id: "scout", name: "Opportunity Scout", role: "Multi-Engine Search (DDG/Tavily)", icon: "compass" },
  { id: "verifier", name: "Eligibility Verifier", role: "Official .edu Portal Deep-Scrape", icon: "shield" },
  { id: "ranker", name: "Matcher / Ranker", role: "Deterministic 5-Pillar Rubric", icon: "award" },
  { id: "writer", name: "Application Writer", role: "SOP & Evidence Matrix Synthesis", icon: "feather" },
  { id: "critic", name: "Fact-Checker", role: "Adversarial Source Quote Audit", icon: "check-circle" },
];

let currentRunId = null;
let lastResult = null;
let eventSource = null;
let allEvents = [];
let currentLogFilter = "all";
let customResumeFile = null;
let customMarksheetFile = null;

const $ = (id) => document.getElementById(id);

// --- TOAST NOTIFICATIONS ---
function showToast(message, type = "info") {
  const container = $("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  const bg = type === "success" ? "bg-emerald-950 border-emerald-500/50 text-emerald-200" :
             type === "error" ? "bg-rose-950 border-rose-500/50 text-rose-200" :
             type === "warning" ? "bg-amber-950 border-amber-500/50 text-amber-200" :
             "bg-indigo-950 border-indigo-500/50 text-indigo-200";
  toast.className = `toast px-4 py-2.5 rounded-xl border shadow-xl text-xs font-medium flex items-center gap-2 ${bg}`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(10px) scale(0.95)";
    setTimeout(() => toast.remove(), 250);
  }, 3500);
}

// --- AGENT CARDS ---
function renderAgentCards() {
  const container = $("agentCards");
  if (!container) return;
  container.innerHTML = AGENTS.map((agent) => `
    <div id="card-${agent.id}" class="agent-card idle rounded-xl border border-slate-800 p-3 flex flex-col justify-between transition-all duration-300">
      <div class="flex items-center justify-between gap-1 mb-2">
        <span class="text-[10px] font-bold uppercase tracking-wider text-slate-400 truncate">${escapeHtml(agent.name)}</span>
        <span id="dot-${agent.id}" class="w-2 h-2 rounded-full bg-slate-600 transition-colors"></span>
      </div>
      <div class="my-1">
        <div id="status-${agent.id}" class="text-xs font-semibold text-slate-300 capitalize">idle</div>
        <div id="subtext-${agent.id}" class="text-[10px] text-slate-500 truncate mt-0.5" title="${agent.role}">${agent.role}</div>
      </div>
    </div>
  `).join("");
}

function setAgentStatus(agentId, status, subtext = "") {
  const card = $("card-" + agentId);
  const statusEl = $("status-" + agentId);
  const dot = $("dot-" + agentId);
  const subEl = $("subtext-" + agentId);
  if (!card || !statusEl) return;

  card.className = `agent-card ${status} rounded-xl border p-3 flex flex-col justify-between transition-all duration-300`;
  statusEl.textContent = status;

  if (dot) {
    if (status === "working") dot.className = "w-2 h-2 rounded-full bg-blue-400 animate-ping";
    else if (status === "done") dot.className = "w-2 h-2 rounded-full bg-emerald-400";
    else if (status === "retrying") dot.className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse";
    else if (status === "failed") dot.className = "w-2 h-2 rounded-full bg-rose-500";
    else dot.className = "w-2 h-2 rounded-full bg-slate-600";
  }

  if (subtext && subEl) {
    subEl.textContent = subtext;
    subEl.title = subtext;
  }
}

// --- LOGGING & SSE STREAM ---
function addEventLine(evt) {
  allEvents.push(evt);
  updateEventCount();
  if (currentLogFilter !== "all" && evt.type !== currentLogFilter) return;
  appendLogElement(evt);
}

function updateEventCount() {
  const badge = $("eventCountBadge");
  if (badge) badge.textContent = `${allEvents.length} events`;
}

function appendLogElement(evt) {
  const log = $("eventLog");
  if (!log) return;
  const line = document.createElement("div");
  line.className = `event-line ${evt.type || ""}`;
  const time = (evt.ts || "").slice(11, 19) || new Date().toISOString().slice(11, 19);
  
  const typeBadge = `<span class="px-1.5 py-0.2 rounded text-[10px] font-mono uppercase bg-slate-800 text-slate-300 mr-1.5">${evt.type || "msg"}</span>`;
  const agentName = `<span class="font-semibold text-indigo-400">${evt.agent || "sys"}</span>:`;
  
  line.innerHTML = `<span class="text-slate-500 mr-2">[${time}]</span> ${typeBadge} ${agentName} <span class="text-slate-200">${escapeHtml(evt.message || "")}</span>`;
  log.appendChild(line);

  const autoScroll = $("autoScrollLog");
  if (!autoScroll || autoScroll.checked) {
    log.scrollTop = log.scrollHeight;
  }
}

function reapplyLogFilter() {
  const log = $("eventLog");
  if (!log) return;
  log.innerHTML = "";
  for (const evt of allEvents) {
    if (currentLogFilter === "all" || evt.type === currentLogFilter) {
      appendLogElement(evt);
    }
  }
}

function handleEvent(evt) {
  if (!evt || !evt.agent) return;
  addEventLine(evt);

  const agent = evt.agent;
  const type = evt.type;
  const msg = evt.message || "";

  if (type === "start" || type === "tool_call") {
    setAgentStatus(agent, "working", msg.slice(0, 42));
  } else if (type === "retry" || type === "fallback") {
    setAgentStatus(agent, "retrying", msg.slice(0, 42));
  } else if (type === "done") {
    setAgentStatus(agent, "done", "Completed");
  } else if (type === "error") {
    setAgentStatus(agent, "failed", msg.slice(0, 42));
  } else if (type === "handoff") {
    setAgentStatus(agent, "done", msg.slice(0, 42));
  } else if (type === "result") {
    setAgentStatus(agent, "working", msg.slice(0, 42));
  }
}

function streamEvents(runId) {
  if (eventSource) eventSource.close();
  allEvents = [];
  $("eventLog").innerHTML = "";
  $("streamIndicator").className = "w-2 h-2 rounded-full bg-emerald-400 animate-pulse";
  $("streamStatusText").textContent = "SSE Stream Active";

  eventSource = new EventSource(`/api/runs/${runId}/events`);
  eventSource.onmessage = (msg) => {
    try {
      const evt = JSON.parse(msg.data);
      if (evt.type === "stream_end") {
        eventSource.close();
        $("streamIndicator").className = "w-2 h-2 rounded-full bg-slate-500";
        $("streamStatusText").textContent = "Run Complete";
        fetchResult(runId);
        return;
      }
      handleEvent(evt);
    } catch (e) {
      console.error("SSE parse error", e);
    }
  };

  eventSource.onerror = () => {
    eventSource.close();
    $("streamIndicator").className = "w-2 h-2 rounded-full bg-amber-500";
    $("streamStatusText").textContent = "Stream Reconnecting / Closed";
    fetchResult(runId);
  };
}

// --- FETCH RESULTS ---
async function fetchResult(runId) {
  $("runStatus").textContent = "Compiling verified dossier...";
  try {
    const resp = await fetch(`/api/runs/${runId}/result`);
    const data = await resp.json();
    if (data.status && !data.profile && data.status !== "failed") {
      $("runStatus").textContent = `Status: ${data.status}...`;
      setTimeout(() => fetchResult(runId), 2000);
      return;
    }
    lastResult = data;
    renderResults(data);
    $("runStatus").textContent = "Verification & Ranking Complete.";
    setRunBtnLoading(false);
    showToast("Scholarship discovery & verification finished!", "success");
  } catch (err) {
    $("runStatus").textContent = "Failed to fetch result: " + err;
    setRunBtnLoading(false);
    showToast("Error retrieving run result", "error");
  }
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// --- RENDERING FACT FIELDS WITH EXPANDABLE SOURCE ACCORDION ---
function fieldHtml(label, field, fieldId) {
  if (!field || !field.value) {
    return `
      <div class="flex items-center justify-between py-1 border-b border-slate-800/40 text-xs">
        <span class="text-slate-400">${label}</span>
        <span class="badge-unverified px-2 py-0.5 rounded text-[10px]">Not stated</span>
      </div>`;
  }

  const isVerified = field.confidence && field.confidence !== "unverified" && field.source_quote;
  const badgeClass = isVerified ? "badge-verified" : "badge-unverified";
  const badgeText = isVerified ? "Verified" : "Unverified";
  const quoteId = `quote-${fieldId || Math.random().toString(36).substring(2, 9)}`;

  const quoteDrawer = field.source_quote ? `
    <button type="button" onclick="toggleQuote('${quoteId}')" class="text-indigo-400 hover:text-indigo-300 text-[11px] underline underline-offset-2 flex items-center gap-1 transition">
      <span>view source quote</span>
      <svg class="w-3 h-3 transition-transform" id="icon-${quoteId}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
      </svg>
    </button>` : "";

  const drawerContent = field.source_quote ? `
    <div id="${quoteId}" class="hidden mt-1.5 p-3 rounded-lg bg-slate-950/90 border border-slate-800 text-[11px] text-slate-300 space-y-1">
      <div class="flex items-center justify-between text-slate-500 text-[10px]">
        <span>Official Extracted Excerpt:</span>
        ${field.fetched_at ? `<span>Fetched: ${escapeHtml(field.fetched_at.slice(0, 16).replace("T", " "))}</span>` : ""}
      </div>
      <blockquote class="italic text-slate-200 border-l-2 border-emerald-500 pl-2 my-1">
        "${escapeHtml(field.source_quote)}"
      </blockquote>
      ${field.source_url ? `
        <div class="pt-1">
          <a href="${escapeHtml(field.source_url)}" target="_blank" rel="noopener" class="text-indigo-400 hover:underline flex items-center gap-1 text-[10px]">
            <span>Open official page</span>
            <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
          </a>
        </div>` : ""}
    </div>` : "";

  return `
    <div class="py-1.5 border-b border-slate-800/40 text-xs">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <span class="text-slate-400 font-medium">${label}:</span>
          <span class="text-slate-100 font-semibold">${escapeHtml(field.value)}</span>
        </div>
        <div class="flex items-center gap-2">
          <span class="${badgeClass} px-2 py-0.5 rounded text-[10px] font-semibold">${badgeText}</span>
          ${quoteDrawer}
        </div>
      </div>
      ${drawerContent}
    </div>`;
}

window.toggleQuote = function(id) {
  const el = $(id);
  const icon = $("icon-" + id);
  if (!el) return;
  const isHidden = el.classList.contains("hidden");
  if (isHidden) {
    el.classList.remove("hidden");
    if (icon) icon.style.transform = "rotate(180deg)";
  } else {
    el.classList.add("hidden");
    if (icon) icon.style.transform = "rotate(0deg)";
  }
};

// --- RENDER COMPLETE RESULTS ---
function renderResults(data) {
  // 1. Profile section
  const profile = data.profile || {};
  const enriched = data.enriched || {};
  const github = enriched.github || {};

  $("profileSection").classList.remove("hidden");
  
  const skillsPills = (profile.skills || []).slice(0, 12).map((s) => 
    `<span class="px-2.5 py-1 rounded-lg bg-indigo-950/60 border border-indigo-500/20 text-indigo-300 text-xs">${escapeHtml(s)}</span>`
  ).join("");

  const githubBadge = github.username ? `
    <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-800/80 border border-slate-700 text-xs">
      <svg class="w-3.5 h-3.5 text-slate-300" fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"/></svg>
      <a href="${escapeHtml(github.profile_url || '')}" target="_blank" rel="noopener" class="text-indigo-400 hover:underline">@${escapeHtml(github.username)}</a>
      <span class="text-slate-400">(${github.public_repos || 0} repos &bull; ${github.total_stars || 0} ★)</span>
    </div>` : "";

  $("profileCard").innerHTML = `
    <div class="grid md:grid-cols-12 gap-6 items-start">
      <div class="md:col-span-8 space-y-3">
        <div class="flex flex-wrap items-center gap-3">
          <h3 class="text-xl font-bold text-white">${escapeHtml(profile.name || "Student")}</h3>
          <span class="px-2.5 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-xs text-slate-300">
            ${escapeHtml(profile.degree || "Degree")} &bull; ${escapeHtml(profile.institution || "Institution")}
          </span>
          ${githubBadge}
        </div>
        <div class="flex flex-wrap items-center gap-2 text-xs text-slate-300">
          <span class="font-semibold text-slate-400">Normalized Grade:</span>
          <span class="px-2 py-0.5 rounded bg-emerald-950/80 border border-emerald-500/30 text-emerald-300 font-bold">
            ${escapeHtml(profile.cgpa_raw || "?")} / ${escapeHtml(profile.cgpa_scale || "10")} (${profile.cgpa_pct != null ? profile.cgpa_pct + "%" : "n/a"} | ${profile.cgpa_4_0 != null ? profile.cgpa_4_0 + "/4.0" : ""})
          </span>
          <span class="text-slate-500">&bull;</span>
          <span class="font-semibold text-slate-400">Target Level:</span>
          <span class="text-slate-200">${escapeHtml(profile.target_level || "Master's")}</span>
          <span class="text-slate-500">&bull;</span>
          <span class="font-semibold text-slate-400">Target Funding:</span>
          <span class="text-slate-200">${escapeHtml(profile.budget || "Fully funded")}</span>
        </div>
        <div class="pt-1">
          <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Verified Skills &amp; Domain Expertise:</div>
          <div class="flex flex-wrap gap-1.5">${skillsPills || "<span class='text-xs text-slate-500'>General Engineering / CS</span>"}</div>
        </div>
      </div>
      <div class="md:col-span-4 bg-slate-950/60 rounded-xl p-4 border border-slate-800/80 space-y-2">
        <div class="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center justify-between">
          <span>Admissions Gaps &amp; Missing Items</span>
          <span class="text-amber-400 text-xs font-normal">Self-healing</span>
        </div>
        ${(profile.missing_fields || []).length ? `
          <ul class="text-xs text-amber-300 space-y-1">
            ${(profile.missing_fields || []).map((m) => `<li class="flex items-start gap-1.5"><span class="text-amber-500 font-bold">&bull;</span><span>${escapeHtml(m)}</span></li>`).join("")}
          </ul>` : `<div class="text-xs text-emerald-400">No critical missing fields detected!</div>`}
        ${(enriched.gaps || []).length ? `
          <div class="pt-2 border-t border-slate-800 text-[11px] text-slate-400">
            <b>Targeted Gaps:</b> ${escapeHtml(enriched.gaps.join("; "))}
          </div>` : ""}
      </div>
    </div>`;

  // 2. Discovered Candidates Table
  const candidates = data.candidates || [];
  if (candidates.length) {
    $("candidatesSection").classList.remove("hidden");
    $("candidateCount").textContent = `${candidates.length} programs`;
    const reportById = Object.fromEntries((data.reports || []).map((r) => [r.candidate_id, r]));

    const renderTable = (filterText = "") => {
      const q = filterText.toLowerCase();
      const filtered = candidates.filter((c) => 
        !q || c.name.toLowerCase().includes(q) || (c.country || "").toLowerCase().includes(q)
      );
      $("candidatesBody").innerHTML = filtered.map((c) => {
        const rep = reportById[c.id] || {};
        const st = rep.status || "unverified";
        const badge = st === "verified" ? `<span class="badge-verified px-2 py-0.5 rounded text-[11px]">Verified</span>` :
                      st === "partial" ? `<span class="badge-unverified px-2 py-0.5 rounded text-[11px]">Partial</span>` :
                      `<span class="bg-slate-800 text-slate-400 px-2 py-0.5 rounded text-[11px]">Unverified</span>`;
        const applyUrl = rep.official_url || c.official_url || c.url || '#';
        const deadlineVal = (rep.deadline && rep.deadline.value) ? escapeHtml(rep.deadline.value) : "<span class='text-slate-500'>Rolling / Check portal</span>";
        return `
          <tr class="hover:bg-slate-900/60 transition">
            <td class="py-3 px-4 font-medium">
              <a href="${escapeHtml(applyUrl)}" target="_blank" rel="noopener" class="text-indigo-400 hover:text-indigo-300 hover:underline flex items-center gap-1.5">
                <span>${escapeHtml(c.name)}</span>
                <svg class="w-3 h-3 text-slate-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
              </a>
            </td>
            <td class="py-3 px-4 text-slate-300 text-xs">${escapeHtml(c.country || "Global")}</td>
            <td class="py-3 px-4 text-xs font-semibold text-amber-300">
              <div class="flex items-center gap-1.5">
                <svg class="w-3.5 h-3.5 text-amber-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
                <span>${deadlineVal}</span>
              </div>
            </td>
            <td class="py-3 px-4">${badge}</td>
            <td class="py-3 px-4">
              <a href="${escapeHtml(applyUrl)}" target="_blank" rel="noopener" class="px-3 py-1 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/40 border border-emerald-500/40 text-emerald-300 text-xs font-semibold inline-flex items-center gap-1.5 transition">
                <span>Apply Link ↗</span>
              </a>
            </td>
          </tr>`;
      }).join("");
    };

    renderTable();
    $("candidateSearchInput").oninput = (e) => renderTable(e.target.value);
  }

  // 3. Top 5 Ranked Recommendations
  const top5 = data.ranked || [];
  if (top5.length) {
    $("top5Section").classList.remove("hidden");
    const candById = Object.fromEntries(candidates.map((c) => [c.id, c]));
    const repById = Object.fromEntries((data.reports || []).map((r) => [r.candidate_id, r]));

    $("top5Cards").innerHTML = top5.map((r, i) => {
      const cand = candById[r.candidate_id] || { name: r.candidate_name, url: "", country: "" };
      const rep = repById[r.candidate_id] || {};
      const bd = r.breakdown || {};
      const applyUrl = rep.official_url || cand.official_url || cand.url || '#';
      const deadlineVal = (rep.deadline && rep.deadline.value) ? rep.deadline.value : "Rolling / See portal";
      const isDeadlineVerified = rep.deadline && rep.deadline.confidence && rep.deadline.confidence !== "unverified" && rep.deadline.source_quote;

      const rankBadge = i === 0 ? "bg-amber-400/20 text-amber-300 border-amber-400/40" :
                        i === 1 ? "bg-slate-300/20 text-slate-200 border-slate-300/40" :
                        i === 2 ? "bg-amber-700/20 text-amber-500 border-amber-700/40" :
                        "bg-indigo-500/10 text-indigo-300 border-indigo-500/20";

      const pillarBars = [
        { key: "fit", label: "Academic Fit", max: 30, cls: "score-bar-fit" },
        { key: "funding", label: "Funding Level", max: 30, cls: "score-bar-funding" },
        { key: "deadline", label: "Deadline Feasibility", max: 15, cls: "score-bar-deadline" },
        { key: "confidence", label: "Source Trust", max: 15, cls: "score-bar-confidence" },
        { key: "risk", label: "Low Risk / Gaps", max: 10, cls: "score-bar-risk" },
      ].map((p) => {
        const val = bd[p.key] ?? 0;
        const pct = Math.min(100, (val / p.max) * 100);
        return `
          <div class="space-y-1">
            <div class="flex items-center justify-between text-[11px]">
              <span class="text-slate-400 font-medium">${p.label}</span>
              <span class="text-slate-300 font-bold">${val} <span class="text-slate-500 text-[10px]">/ ${p.max}</span></span>
            </div>
            <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
              <div class="${p.cls} h-1.5 rounded-full transition-all duration-500" style="width: ${pct}%"></div>
            </div>
          </div>`;
      }).join("");

      return `
        <div class="glass-panel glass-panel-interactive rounded-2xl p-6 sm:p-7 space-y-5 border border-slate-800">
          <!-- Card Header -->
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="space-y-2.5 max-w-2xl">
              <!-- Badges Row: Rank, Country, Verification, and Prominent Last Date to Apply -->
              <div class="flex flex-wrap items-center gap-2">
                <span class="px-2.5 py-0.5 rounded-full border text-xs font-black ${rankBadge}">Rank #${i + 1}</span>
                <span class="text-xs text-slate-400">&bull;</span>
                <span class="text-xs text-slate-400 font-medium">${escapeHtml(cand.country || "Global")}</span>
                ${rep.status === "verified" ? `<span class="badge-verified px-2 py-0.5 rounded text-[10px] font-semibold">100% Official Portal Verified</span>` : ""}
                
                <!-- PROMINENT LAST DATE TO APPLY CALLOUT -->
                <div class="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-gradient-to-r from-amber-950/80 to-rose-950/80 border border-amber-500/40 text-amber-200 text-xs font-semibold shadow-sm">
                  <svg class="w-3.5 h-3.5 text-amber-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                  </svg>
                  <span>Last Date to Apply: <strong class="text-white">${escapeHtml(deadlineVal)}</strong></span>
                  ${isDeadlineVerified ? `<span class="inline-block w-2 h-2 rounded-full bg-emerald-400 ml-0.5" title="Verified from official page"></span>` : ''}
                </div>
              </div>

              <!-- Scholarship Title -->
              <h3 class="text-xl font-bold text-white tracking-tight">${escapeHtml(cand.name)}</h3>

              <!-- DIRECT APPLY LINK IN HEADER -->
              <div class="flex flex-wrap items-center gap-3 pt-1">
                <a href="${escapeHtml(applyUrl)}" target="_blank" rel="noopener" class="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold text-white bg-gradient-to-r from-emerald-600 via-teal-600 to-emerald-600 hover:from-emerald-500 hover:to-teal-500 shadow-lg shadow-emerald-600/30 transition-all hover:scale-[1.02] active:scale-[0.98]">
                  <svg class="w-4 h-4 text-emerald-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
                  </svg>
                  <span>Direct Link to Apply for Scholarship</span>
                </a>
                <span class="text-xs text-slate-400">Portal: <span class="font-mono text-slate-300 text-[11px]">${escapeHtml(applyUrl.split('/')[2] || 'official portal')}</span></span>
              </div>
            </div>

            <!-- Overall Score Pill -->
            <div class="text-right bg-slate-950/80 border border-slate-800 rounded-xl p-3.5 shadow-sm min-w-[110px]">
              <div class="text-3xl font-black text-gradient">${r.total_score}</div>
              <div class="text-[10px] font-bold uppercase tracking-wider text-slate-400">Total Score / 100</div>
            </div>
          </div>

          <!-- Why it Fits -->
          <div class="bg-indigo-950/30 border border-indigo-500/20 rounded-xl p-3.5 text-xs text-indigo-200 leading-relaxed">
            <span class="font-bold text-indigo-300">Why this fits your profile:</span> ${escapeHtml(r.why_fits || "Strong academic and background match.")}
          </div>

          <!-- 5-Factor Score Breakdown Bars -->
          <div class="grid sm:grid-cols-2 lg:grid-cols-5 gap-3 pt-1">
            ${pillarBars}
          </div>

          <!-- Verified Facts Grid with Expandable Quotes -->
          <div class="pt-3 border-t border-slate-800 space-y-1">
            <div class="text-xs font-bold text-slate-300 uppercase tracking-wider mb-2">Verified Admissions Facts &amp; Requirements:</div>
            ${fieldHtml("Funding Coverage", rep.funding, `fund-${i}`)}
            <div class="p-2 rounded-lg bg-amber-950/20 border border-amber-500/20 my-1">
              ${fieldHtml("Application Deadline (Last Date to Apply)", rep.deadline, `dl-${i}`)}
            </div>
            ${fieldHtml("Minimum GPA / Grade", rep.min_grade, `grade-${i}`)}
            ${fieldHtml("Degree Requirement", rep.degree_req, `deg-${i}`)}
            ${fieldHtml("English Proficiency", rep.english_req, `eng-${i}`)}
            ${fieldHtml("How to Apply", rep.how_to_apply, `app-${i}`)}
          </div>

          <!-- Gaps & Action Bar with Direct Apply Button -->
          <div class="flex flex-wrap items-center justify-between gap-4 pt-3 border-t border-slate-800/80 text-xs">
            <div class="space-y-1">
              ${(rep.unmet || []).length ? `<div class="text-rose-400 font-medium">⚠ Gaps: ${escapeHtml(rep.unmet.join("; "))}</div>` : `<div class="text-emerald-400 font-medium">✓ No hard eligibility disqualifiers found</div>`}
              ${(rep.unknown || []).length ? `<div class="text-amber-400/80 text-[11px]">Unverified factors: ${escapeHtml(rep.unknown.slice(0, 3).join("; "))}</div>` : ""}
            </div>
            <!-- Dual Action Buttons -->
            <div class="flex flex-wrap items-center gap-2.5">
              <a href="${escapeHtml(applyUrl)}" target="_blank" rel="noopener" class="px-4 py-2 rounded-xl font-bold text-xs bg-emerald-600 hover:bg-emerald-500 text-white shadow-md shadow-emerald-600/25 flex items-center gap-1.5 transition">
                <svg class="w-3.5 h-3.5 text-emerald-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
                <span>Direct Apply Link ↗</span>
              </a>
              <button type="button" onclick="generateSopForCandidate('${r.candidate_id}', '${escapeHtml(cand.name).replace(/'/g, "\\'")}')" class="px-4 py-2 rounded-xl font-bold text-xs bg-indigo-600 hover:bg-indigo-500 text-white shadow-md shadow-indigo-600/20 flex items-center gap-1.5 transition">
                <svg class="w-3.5 h-3.5 text-indigo-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
                <span>Draft Statement of Purpose for this Opportunity</span>
              </button>
            </div>
          </div>
        </div>`;
    }).join("");
  }

  // 4. SOP Draft & Checklist
  if (data.sop) {
    renderSop(data.sop);
  }
}

function renderSop(sop) {
  $("sopSection").classList.remove("hidden");
  $("sopEditor").value = sop.sop_markdown || "";
  renderFormattedMarkdown(sop.sop_markdown || "");

  // Evidence Map
  const mapEl = $("evidenceMap");
  if (mapEl) {
    mapEl.innerHTML = (sop.evidence_map || []).map((e) => `
      <div class="p-2.5 rounded-lg bg-slate-900/80 border border-slate-800/80 text-xs space-y-1">
        <div class="font-medium text-slate-200">&bull; ${escapeHtml(e.claim)}</div>
        <div class="text-[11px] text-slate-400 flex items-center justify-between">
          <span class="text-indigo-400 font-mono text-[10px]">${escapeHtml(e.source_url)}</span>
          ${e.snippet ? `<span class="italic text-slate-500">"${escapeHtml(e.snippet.slice(0, 60))}"</span>` : ""}
        </div>
      </div>
    `).join("") || "<div class='text-xs text-slate-500'>Evidence claims mapped to resume facts.</div>";
  }

  // Checklist
  const checkEl = $("checklist");
  if (checkEl) {
    checkEl.innerHTML = (sop.checklist || []).map((i) => {
      const isMissing = i.status === "missing";
      const icon = isMissing ? `<span class="text-rose-400 font-bold">✕</span>` : `<span class="text-emerald-400 font-bold">✓</span>`;
      const badgeCls = isMissing ? "badge-missing" : "badge-verified";
      return `
        <div class="flex items-center justify-between p-2.5 rounded-lg bg-slate-900/80 border border-slate-800/80 text-xs mb-1.5">
          <div class="flex items-center gap-2">
            ${icon}
            <span class="text-slate-200 font-medium">${escapeHtml(i.item)}</span>
          </div>
          <span class="${badgeCls} px-2 py-0.5 rounded text-[10px] font-semibold capitalize">${escapeHtml(i.note || i.status)}</span>
        </div>`;
    }).join("") || "<div class='text-xs text-slate-500'>Checklist extracted from portal.</div>";
  }
}

// Simple fast markdown parser for preview
function renderFormattedMarkdown(md) {
  const container = $("sopFormattedView");
  if (!container) return;
  let html = escapeHtml(md);

  // Headers
  html = html.replace(/^### (.*$)/gim, '<h3 class="text-base font-bold text-slate-200 mt-4 mb-1">$1</h3>');
  html = html.replace(/^## (.*$)/gim, '<h2 class="text-lg font-bold text-indigo-300 mt-5 mb-2 pb-1 border-b border-slate-800">$1</h2>');
  html = html.replace(/^# (.*$)/gim, '<h1 class="text-xl font-extrabold text-white mb-4 pb-2 border-b border-indigo-500/30">$1</h1>');

  // Bold
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong class="text-white font-semibold">$1</strong>');
  // Italic
  html = html.replace(/\*(.*?)\*/g, '<em class="text-slate-300">$1</em>');

  // Blockquotes
  html = html.replace(/^\> (.*$)/gim, '<blockquote class="border-l-4 border-indigo-500 pl-3 py-1 my-2 text-slate-400 italic bg-indigo-950/20 rounded-r">$1</blockquote>');

  // Lists
  html = html.replace(/^[•\-\*] (.*$)/gim, '<li class="ml-4 list-disc text-slate-300">$1</li>');

  // Paragraph breaks
  html = html.replace(/\n\n/g, '</p><p class="my-3 leading-relaxed text-slate-300">');
  html = `<p class="my-3 leading-relaxed text-slate-300">${html}</p>`;

  container.innerHTML = html;
}

// Regenerate SOP for specific candidate
window.generateSopForCandidate = async function(candId, candName) {
  if (!currentRunId) {
    showToast("Please run a discovery pipeline first.", "warning");
    return;
  }
  showToast(`Regenerating SOP for ${candName}...`, "info");
  try {
    const resp = await fetch(`/api/runs/${currentRunId}/sop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_id: candId }),
    });
    const data = await resp.json();
    if (data.sop) {
      if (lastResult) lastResult.sop = data.sop;
      renderSop(data.sop);
      showToast(`Statement of Purpose tailored for ${candName}!`, "success");
      $("sopSection").scrollIntoView({ behavior: "smooth" });
    } else {
      throw new Error(data.detail || "SOP generation failed");
    }
  } catch (err) {
    showToast(`Failed to generate SOP: ${err.message}`, "error");
  }
};

// --- RUN BUTTON & PIPELINE EXECUTION ---
function setRunBtnLoading(loading) {
  const btn = $("runBtn");
  const icon = $("runIcon");
  const spinner = $("runSpinner");
  if (!btn) return;
  btn.disabled = loading;
  if (loading) {
    if (icon) icon.classList.add("hidden");
    if (spinner) spinner.classList.remove("hidden");
  } else {
    if (icon) icon.classList.remove("hidden");
    if (spinner) spinner.classList.add("hidden");
  }
}

$("runBtn").addEventListener("click", async () => {
  const resumeFile = customResumeFile || $("resume").files[0];
  if (!resumeFile && !$("demoMode").checked) {
    $("runStatus").textContent = "Please upload a resume PDF first or click 'Load Sample'.";
    showToast("Resume PDF is required", "error");
    return;
  }

  const form = new FormData();
  if (resumeFile) form.append("resume", resumeFile);
  const marksheetFile = customMarksheetFile || $("marksheet").files[0];
  if (marksheetFile) form.append("marksheet", marksheetFile);

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

  setRunBtnLoading(true);
  $("runStatus").textContent = "Orchestrating agent swarm...";
  $("timelineSection").classList.remove("hidden");
  $("timelineSection").scrollIntoView({ behavior: "smooth" });
  renderAgentCards();

  try {
    const resp = await fetch("/api/runs", { method: "POST", body: form });
    const data = await resp.json();
    if (!data.run_id) throw new Error(data.detail || JSON.stringify(data));
    currentRunId = data.run_id;
    $("runStatus").textContent = `Run ${currentRunId}${data.demo_mode ? " (demo replay)" : ""}`;
    showToast(`Started agent swarm for run ${currentRunId}`, "info");
    streamEvents(currentRunId);
  } catch (err) {
    $("runStatus").textContent = "Failed to start: " + err;
    setRunBtnLoading(false);
    showToast("Error starting agent swarm: " + err.message, "error");
  }
});

// --- LOAD SAMPLE PERSONA (RAHUL) ---
$("loadSampleBtn").addEventListener("click", async () => {
  $("loadSampleBtn").disabled = true;
  $("loadSampleBtn").classList.add("opacity-60");
  showToast("Loading Rahul Sharma sample profile...", "info");

  try {
    // 1. Load preferences
    const profResp = await fetch("/api/sample/profile");
    if (profResp.ok) {
      const prof = await profResp.json();
      $("countries").value = prof.countries || "Canada, Germany, Singapore";
      $("level").value = prof.level || "Master's";
      $("budget").value = prof.budget || "fully funded";
      $("github").value = prof.github_url || "https://github.com/rahul-ai-scholar";
      $("extraLinks").value = prof.extra_links || "";
    }

    // 2. Fetch sample Resume PDF
    const resumeResp = await fetch("/api/sample/files/resume.pdf");
    if (resumeResp.ok) {
      const blob = await resumeResp.blob();
      customResumeFile = new File([blob], "rahul_resume.pdf", { type: "application/pdf" });
      $("resumeFileName").textContent = "rahul_resume.pdf";
      $("resumeFileSize").textContent = `${(blob.size / 1024).toFixed(1)} KB &bull; Loaded from sample data`;
      $("resumeBadge").textContent = "Sample Loaded";
      $("resumeBadge").className = "text-[11px] text-emerald-400 font-semibold";
    }

    // 3. Fetch sample Marksheet PDF
    const msResp = await fetch("/api/sample/files/marksheet.pdf");
    if (msResp.ok) {
      const blob = await msResp.blob();
      customMarksheetFile = new File([blob], "rahul_marksheet.pdf", { type: "application/pdf" });
      $("marksheetFileName").textContent = "rahul_marksheet.pdf";
      $("marksheetFileSize").textContent = `${(blob.size / 1024).toFixed(1)} KB &bull; Loaded from sample data`;
      $("marksheetBadge").textContent = "Sample Loaded";
      $("marksheetBadge").className = "text-[11px] text-emerald-400 font-semibold";
    }

    showToast("Loaded sample persona: Rahul Sharma (IIT Grad)", "success");
  } catch (err) {
    showToast("Error loading sample: " + err.message, "error");
  } finally {
    $("loadSampleBtn").disabled = false;
    $("loadSampleBtn").classList.remove("opacity-60");
  }
});

// --- DRAG & DROP FILE HANDLING ---
function setupDropzone(dropzoneId, fileInputId, fileNameId, fileSizeId, onFileSelected) {
  const zone = $(dropzoneId);
  const input = $(fileInputId);
  if (!zone || !input) return;

  zone.addEventListener("click", () => input.click());

  input.addEventListener("change", (e) => {
    if (input.files && input.files[0]) {
      const f = input.files[0];
      $(fileNameId).textContent = f.name;
      $(fileSizeId).textContent = `${(f.size / 1024).toFixed(1)} KB`;
      onFileSelected(f);
    }
  });

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("dragover");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const f = e.dataTransfer.files[0];
      if (f.type === "application/pdf" || f.name.endsWith(".pdf")) {
        $(fileNameId).textContent = f.name;
        $(fileSizeId).textContent = `${(f.size / 1024).toFixed(1)} KB`;
        onFileSelected(f);
      } else {
        showToast("Please drop a PDF document", "warning");
      }
    }
  });
}

setupDropzone("resumeDropzone", "resume", "resumeFileName", "resumeFileSize", (f) => {
  customResumeFile = f;
  $("resumeBadge").textContent = "File Selected";
  $("resumeBadge").className = "text-[11px] text-emerald-400 font-semibold";
});

setupDropzone("marksheetDropzone", "marksheet", "marksheetFileName", "marksheetFileSize", (f) => {
  customMarksheetFile = f;
  $("marksheetBadge").textContent = "File Selected";
  $("marksheetBadge").className = "text-[11px] text-emerald-400 font-semibold";
});

// Country preset clicks
document.querySelectorAll(".country-tag").forEach((btn) => {
  btn.addEventListener("click", () => {
    const country = btn.dataset.country;
    const input = $("countries");
    const current = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!current.includes(country)) {
      current.push(country);
      input.value = current.join(", ");
    }
  });
});

// Log filters
document.querySelectorAll(".filter-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach((b) => {
      b.classList.remove("bg-indigo-600", "text-white", "font-medium");
      b.classList.add("bg-slate-800", "text-slate-300");
    });
    btn.classList.add("bg-indigo-600", "text-white", "font-medium");
    btn.classList.remove("bg-slate-800", "text-slate-300");
    currentLogFilter = btn.dataset.filter;
    reapplyLogFilter();
  });
});

$("clearLogBtn").addEventListener("click", () => {
  $("eventLog").innerHTML = "";
  allEvents = [];
  updateEventCount();
});

// SOP Tab view toggling
$("viewFormattedSopBtn").addEventListener("click", () => {
  $("viewFormattedSopBtn").className = "px-3 py-1.5 rounded-md bg-indigo-600 text-white font-medium transition";
  $("viewRawSopBtn").className = "px-3 py-1.5 rounded-md text-slate-300 hover:text-white transition";
  $("sopFormattedView").classList.remove("hidden");
  $("sopEditor").classList.add("hidden");
  renderFormattedMarkdown($("sopEditor").value);
});

$("viewRawSopBtn").addEventListener("click", () => {
  $("viewRawSopBtn").className = "px-3 py-1.5 rounded-md bg-indigo-600 text-white font-medium transition";
  $("viewFormattedSopBtn").className = "px-3 py-1.5 rounded-md text-slate-300 hover:text-white transition";
  $("sopFormattedView").classList.add("hidden");
  $("sopEditor").classList.remove("hidden");
});

$("copySopBtn").addEventListener("click", () => {
  navigator.clipboard.writeText($("sopEditor").value);
  showToast("Statement of Purpose copied to clipboard!", "success");
});

$("downloadSop").addEventListener("click", () => {
  const blob = new Blob([$("sopEditor").value], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `ScholarSwarm_SOP_${currentRunId || 'draft'}.md`;
  a.click();
  showToast("Downloaded SOP draft (.md)", "success");
});

$("downloadReport").addEventListener("click", () => {
  if (!currentRunId) {
    showToast("Please run a pipeline first to export a report.", "warning");
    return;
  }
  window.open(`/api/runs/${currentRunId}/export`, "_blank");
});

// Initial agent cards render
renderAgentCards();
