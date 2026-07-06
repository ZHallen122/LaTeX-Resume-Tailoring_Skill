const state = {
  previewMode: "resume",
  reviewMode: "ats",
  analysis: null,
};

const stopWords = new Set([
  "and",
  "the",
  "with",
  "for",
  "from",
  "that",
  "this",
  "will",
  "you",
  "your",
  "are",
  "our",
  "has",
  "have",
  "using",
  "into",
  "role",
  "team",
  "work",
  "experience",
  "software",
  "engineer",
  "senior",
]);

const knownSkills = [
  "python",
  "typescript",
  "javascript",
  "java",
  "go",
  "rust",
  "react",
  "node",
  "aws",
  "gcp",
  "azure",
  "docker",
  "kubernetes",
  "postgresql",
  "mysql",
  "redis",
  "graphql",
  "rest",
  "api",
  "apis",
  "distributed",
  "observability",
  "scalability",
  "reliability",
  "security",
  "machine learning",
  "ml",
  "data",
  "backend",
  "frontend",
  "full stack",
  "product",
  "leadership",
  "ci/cd",
  "terraform",
  "lambda",
  "microservices",
];

const els = {
  jd: document.getElementById("jobDescription"),
  vault: document.getElementById("careerVault"),
  latex: document.getElementById("latexResume"),
  run: document.getElementById("runReview"),
  copy: document.getElementById("copyReport"),
  resumePreview: document.getElementById("resumePreview"),
  coveragePreview: document.getElementById("coveragePreview"),
  resumeStats: document.getElementById("resumeStats"),
  reviewScore: document.getElementById("reviewScore"),
  reviewOutput: document.getElementById("reviewOutput"),
};

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return map[char];
  });
}

function cleanLatexText(input) {
  return input
    .replace(/%.*$/gm, "")
    .replace(/\\href\{([^}]*)\}\{([^}]*)\}/g, "$2")
    .replace(/\\textbf\{([^}]*)\}/g, "$1")
    .replace(/\\emph\{([^}]*)\}/g, "$1")
    .replace(/\\item\s*/g, "• ")
    .replace(/\\[a-zA-Z]+\*?(?:\[[^\]]*\])?/g, "")
    .replace(/[{}]/g, "")
    .replace(/\\&/g, "&")
    .replace(/\\%/g, "%")
    .replace(/\\_/g, "_")
    .replace(/\s+/g, " ")
    .trim();
}

function parseResume(latex) {
  const sections = [];
  const sectionPattern = /\\section\*?\{([^}]*)\}/g;
  const matches = [...latex.matchAll(sectionPattern)];

  if (!matches.length) {
    const text = cleanLatexText(latex);
    return [{ title: "Resume", lines: text ? [text] : [] }];
  }

  matches.forEach((match, index) => {
    const start = match.index + match[0].length;
    const end = matches[index + 1]?.index ?? latex.length;
    const body = latex.slice(start, end);
    const items = [...body.matchAll(/\\(?:resumeItem|item)\{([^]*?)\}/g)].map((m) =>
      cleanLatexText(m[1])
    );
    const cleanedLines = cleanLatexText(body)
      .split(/(?:•|\n)/)
      .map((line) => line.trim())
      .filter(Boolean);
    const lines = items.length ? items : cleanedLines;
    sections.push({ title: cleanLatexText(match[1]), lines });
  });

  return sections;
}

function extractKeywords(text) {
  const lower = text.toLowerCase();
  const foundSkills = knownSkills.filter((skill) => lower.includes(skill));
  const words = lower
    .replace(/[^a-z0-9+#./-]+/g, " ")
    .split(/\s+/)
    .filter((word) => word.length > 3 && !stopWords.has(word));
  const counts = new Map();
  words.forEach((word) => counts.set(word, (counts.get(word) || 0) + 1));
  const frequent = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 16)
    .map(([word]) => word);
  return [...new Set([...foundSkills, ...frequent])].slice(0, 24);
}

function scoreCoverage(keywords, resumeText, vaultText) {
  const resume = resumeText.toLowerCase();
  const vault = vaultText.toLowerCase();
  return keywords.map((keyword) => {
    const value = keyword.toLowerCase();
    if (resume.includes(value)) return { keyword, status: "hit" };
    if (vault.includes(value)) return { keyword, status: "partial" };
    return { keyword, status: "miss" };
  });
}

function analyze() {
  const sections = parseResume(els.latex.value);
  const resumeText = sections.flatMap((section) => section.lines).join(" ");
  const keywords = extractKeywords(els.jd.value);
  const coverage = scoreCoverage(keywords, resumeText, els.vault.value);
  const hits = coverage.filter((item) => item.status === "hit").length;
  const partials = coverage.filter((item) => item.status === "partial").length;
  const misses = coverage.filter((item) => item.status === "miss").length;
  const bullets = sections.reduce((total, section) => total + section.lines.length, 0);
  const score = keywords.length ? Math.round(((hits + partials * 0.55) / keywords.length) * 100) : 0;

  state.analysis = { sections, resumeText, keywords, coverage, hits, partials, misses, bullets, score };
  renderAll();
}

function renderResume() {
  const { sections, bullets } = state.analysis;
  els.resumeStats.textContent = `${sections.length} sections · ${bullets} bullets`;
  els.resumePreview.innerHTML = sections
    .map((section) => {
      const lines = section.lines.length
        ? `<ul>${section.lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>`
        : `<p class="empty">No readable content detected.</p>`;
      return `<section class="resume-section"><h3>${escapeHtml(section.title)}</h3>${lines}</section>`;
    })
    .join("");
}

function renderCoverage() {
  const { coverage } = state.analysis;
  els.coveragePreview.innerHTML = coverage.length
    ? `<div class="keyword-grid">${coverage
        .map(
          (item) =>
            `<div class="keyword"><strong>${escapeHtml(item.keyword)}</strong><span class="status ${item.status}">${item.status}</span></div>`
        )
        .join("")}</div>`
    : `<p class="empty">No JD keywords detected.</p>`;
}

function renderScore() {
  const { score, hits, partials, misses } = state.analysis;
  els.reviewScore.textContent = `${score}% coverage · ${hits} matched · ${partials} vault-only · ${misses} gaps`;
}

function listItems(items) {
  if (!items.length) return `<p class="empty">None detected.</p>`;
  return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function topMisses(limit = 6) {
  return state.analysis.coverage
    .filter((item) => item.status === "miss")
    .slice(0, limit)
    .map((item) => item.keyword);
}

function vaultOnly(limit = 6) {
  return state.analysis.coverage
    .filter((item) => item.status === "partial")
    .slice(0, limit)
    .map((item) => item.keyword);
}

function matched(limit = 8) {
  return state.analysis.coverage
    .filter((item) => item.status === "hit")
    .slice(0, limit)
    .map((item) => item.keyword);
}

function renderMeter(label, value, riskClass = "") {
  return `<div class="score-row ${riskClass}">
    <strong>${label}</strong>
    <div class="meter"><span style="width:${Math.max(0, Math.min(100, value))}%"></span></div>
    <span>${value}%</span>
  </div>`;
}

function renderReview() {
  const { score, misses, partials, bullets } = state.analysis;
  const mode = state.reviewMode;
  const missing = topMisses();
  const supported = matched();
  const available = vaultOnly();
  const density = bullets > 12 ? 84 : bullets > 7 ? 68 : 48;
  const risk = Math.min(100, Math.round((misses * 9) + (partials * 4)));

  const views = {
    ats: `
      <section><h3>ATS Read</h3>${renderMeter("Coverage", score)}${listItems([
        `Strongest matched terms: ${supported.join(", ") || "none"}.`,
        `Vault-supported terms missing from resume: ${available.join(", ") || "none"}.`,
        `Unsupported JD gaps: ${missing.join(", ") || "none"}.`,
      ])}</section>
      <section><h3>Parsing Notes</h3>${listItems([
        "Keep standard section headings and avoid replacing the LaTeX template.",
        "Put core JD terms inside experience/project bullets where the vault supports them.",
        "Do not add unsupported keywords just to raise coverage.",
      ])}</section>`,
    recruiter: `
      <section><h3>HR Scan</h3>${renderMeter("Scan Fit", Math.max(20, score - 6))}${listItems([
        `The resume has ${supported.length} immediately visible role-aligned signals.`,
        available.length ? `Move ${available.slice(0, 3).join(", ")} into visible bullets if truthful.` : "No obvious vault-only keywords need promotion.",
        missing.length ? `Recruiter may miss fit for ${missing.slice(0, 3).join(", ")}.` : "No major quick-scan gaps detected.",
      ])}</section>
      <section><h3>Message</h3>${listItems([
        "Lead with the most role-relevant backend, product, reliability, or platform work.",
        "Prefer concrete outcomes over broad claims.",
        "Keep each bullet readable in one scan pass.",
      ])}</section>`,
    sde: `
      <section><h3>Senior SDE Signal</h3>${renderMeter("Depth", Math.max(15, Math.min(95, score - 2 + density / 10)))}${listItems([
        "Best bullets should expose system, data, API, reliability, or scaling decisions.",
        "Metrics are valuable only if the vault supports them.",
        available.length ? `Technical depth candidates from vault: ${available.slice(0, 4).join(", ")}.` : "Resume already carries most detected technical terms.",
      ])}</section>
      <section><h3>Interview Hooks</h3>${listItems([
        "Turn generic tool mentions into implementation-specific bullets.",
        "Clarify ownership level without implying unsupported architecture authority.",
        "Make production impact and tradeoffs visible where real.",
      ])}</section>`,
    integrity: `
      <section><h3>Risk Report</h3>
        ${renderMeter("Hallucination", risk, risk > 60 ? "risk-high" : risk > 30 ? "risk-mid" : "")}
        ${renderMeter("Keyword Stuffing", Math.min(100, Math.max(8, partials * 10)), partials > 5 ? "risk-mid" : "")}
        ${renderMeter("Format Drift", 12)}
      </section>
      <section><h3>Integrity Notes</h3>${listItems([
        missing.length ? `Do not claim unsupported JD terms: ${missing.join(", ")}.` : "No unsupported JD terms detected by this pass.",
        available.length ? `These terms exist in the vault but need careful wording: ${available.join(", ")}.` : "No vault-only claims require promotion.",
        "Any new metric, scale, title, ownership, production, security, or compliance claim must come from the vault.",
      ])}</section>`,
    rewrite: `
      <section><h3>Patch Ideas</h3>${listItems([
        ...available.slice(0, 5).map((term) => `Add a truthful bullet reference for "${term}" using career vault evidence.`),
        ...missing.slice(0, 4).map((term) => `Leave "${term}" as a JD gap unless the user provides verified evidence.`),
        "Shorten weaker bullets before changing LaTeX spacing if the resume exceeds one page.",
      ])}</section>
      <section><h3>Candidate Bullet Shape</h3>${listItems([
        "Action verb + verified technical method + scope + supported result.",
        "One JD keyword per natural idea, not a cluster of unrelated terms.",
        "Preserve existing LaTeX macros and section order.",
      ])}</section>`,
  };

  els.reviewOutput.innerHTML = views[mode];
}

function renderPreviewMode() {
  els.resumePreview.classList.toggle("hidden", state.previewMode !== "resume");
  els.coveragePreview.classList.toggle("hidden", state.previewMode !== "coverage");
}

function renderAll() {
  renderResume();
  renderCoverage();
  renderScore();
  renderReview();
  renderPreviewMode();
}

function currentReportText() {
  const { score, hits, partials, misses } = state.analysis;
  return [
    `Resume review: ${score}% coverage`,
    `Matched: ${hits}`,
    `Vault-only: ${partials}`,
    `Gaps: ${misses}`,
    "",
    els.reviewOutput.innerText.trim(),
  ].join("\n");
}

document.querySelectorAll("[data-preview]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-preview]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.previewMode = button.dataset.preview;
    renderPreviewMode();
  });
});

document.querySelectorAll("[data-review]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-review]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.reviewMode = button.dataset.review;
    renderReview();
  });
});

[els.jd, els.vault, els.latex].forEach((input) => {
  input.addEventListener("input", () => analyze());
});

els.run.addEventListener("click", analyze);
els.copy.addEventListener("click", async () => {
  if (!state.analysis) analyze();
  const report = currentReportText();
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(report);
  } else {
    const scratch = document.createElement("textarea");
    scratch.value = report;
    scratch.setAttribute("readonly", "");
    scratch.style.position = "fixed";
    scratch.style.left = "-9999px";
    document.body.appendChild(scratch);
    scratch.select();
    document.execCommand("copy");
    scratch.remove();
  }
  els.copy.textContent = "Copied";
  window.setTimeout(() => {
    els.copy.textContent = "Copy";
  }, 1100);
});

analyze();
