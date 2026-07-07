const state = {
  previewMode: "compare",
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
  original: document.getElementById("originalLatex"),
  latex: document.getElementById("latexResume"),
  run: document.getElementById("runReview"),
  copy: document.getElementById("copyReport"),
  comparePreview: document.getElementById("comparePreview"),
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

function normalizeForCompare(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9+#./%-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function significantWords(value) {
  return normalizeForCompare(value)
    .split(" ")
    .filter((word) => word.length > 1 && !stopWords.has(word));
}

function lineSimilarity(a, b) {
  const aWords = new Set(significantWords(a));
  const bWords = new Set(significantWords(b));
  const union = new Set([...aWords, ...bWords]);
  if (!union.size) return 0;
  let overlap = 0;
  aWords.forEach((word) => {
    if (bWords.has(word)) overlap += 1;
  });
  return overlap / union.size;
}

function closestLine(line, candidates) {
  return candidates.reduce(
    (best, candidate) => {
      const score = lineSimilarity(line, candidate);
      return score > best.score ? { line: candidate, score } : best;
    },
    { line: "", score: 0 }
  );
}

function sectionKey(title) {
  return normalizeForCompare(title) || "resume";
}

function flattenLines(sections) {
  return sections.flatMap((section) => section.lines);
}

function buildResumeDiff(originalSections, currentSections) {
  const originalBySection = new Map();
  const currentBySection = new Map();
  const originalLines = flattenLines(originalSections);
  const originalLineKeys = new Set(originalLines.map(normalizeForCompare));
  const usedOriginalKeys = new Set();

  originalSections.forEach((section) => {
    originalBySection.set(sectionKey(section.title), section);
  });
  currentSections.forEach((section) => {
    currentBySection.set(sectionKey(section.title), section);
  });

  const sections = currentSections.map((section) => {
    const key = sectionKey(section.title);
    const originalSection = originalBySection.get(key);
    const sectionCandidates = originalSection?.lines.length ? originalSection.lines : originalLines;
    const rows = section.lines.map((line) => {
      const lineKey = normalizeForCompare(line);
      if (originalLineKeys.has(lineKey)) {
        usedOriginalKeys.add(lineKey);
        return { type: "same", line };
      }

      const match = closestLine(line, sectionCandidates);
      if (match.score >= 0.34) {
        usedOriginalKeys.add(normalizeForCompare(match.line));
        return { type: "changed", line, previous: match.line };
      }

      return { type: "added", line };
    });

    const currentKeys = new Set(section.lines.map(normalizeForCompare));
    const removed = (originalSection?.lines || []).filter((line) => {
      const keyForLine = normalizeForCompare(line);
      return keyForLine && !currentKeys.has(keyForLine) && !usedOriginalKeys.has(keyForLine);
    });

    return { title: section.title, rows, removed };
  });

  originalSections.forEach((section) => {
    const key = sectionKey(section.title);
    if (currentBySection.has(key)) return;
    sections.push({
      title: section.title,
      rows: [],
      removed: section.lines,
    });
  });

  const added = sections.reduce(
    (total, section) => total + section.rows.filter((row) => row.type === "added").length,
    0
  );
  const changed = sections.reduce(
    (total, section) => total + section.rows.filter((row) => row.type === "changed").length,
    0
  );
  const removed = sections.reduce((total, section) => total + section.removed.length, 0);

  return { sections, added, changed, removed };
}

function tokenizeForDiff(value) {
  return value.match(/[A-Za-z0-9+#./%-]+|[^A-Za-z0-9+#./%-]+/g) || [];
}

function tokenKey(token) {
  return /[A-Za-z0-9]/.test(token) ? normalizeForCompare(token) : "";
}

function significantTokenIndexes(tokens) {
  return tokens
    .map((token, index) => ({ token, index, key: tokenKey(token) }))
    .filter((item) => item.key);
}

function currentTokenMatches(previous, current) {
  const previousTokens = significantTokenIndexes(tokenizeForDiff(previous));
  const currentTokens = significantTokenIndexes(tokenizeForDiff(current));
  const table = Array.from({ length: previousTokens.length + 1 }, () =>
    Array(currentTokens.length + 1).fill(0)
  );

  for (let i = previousTokens.length - 1; i >= 0; i -= 1) {
    for (let j = currentTokens.length - 1; j >= 0; j -= 1) {
      table[i][j] =
        previousTokens[i].key === currentTokens[j].key
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const matches = new Set();
  let i = 0;
  let j = 0;
  while (i < previousTokens.length && j < currentTokens.length) {
    if (previousTokens[i].key === currentTokens[j].key) {
      matches.add(currentTokens[j].index);
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      i += 1;
    } else {
      j += 1;
    }
  }

  return matches;
}

function renderChangedText(previous, current) {
  const tokens = tokenizeForDiff(current);
  const matches = currentTokenMatches(previous, current);
  return tokens
    .map((token, index) => {
      if (!tokenKey(token) || matches.has(index)) return escapeHtml(token);
      return `<mark class="diff-token-added">${escapeHtml(token)}</mark>`;
    })
    .join("");
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
  const originalSections = parseResume(els.original.value);
  const sections = parseResume(els.latex.value);
  const resumeText = sections.flatMap((section) => section.lines).join(" ");
  const keywords = extractKeywords(els.jd.value);
  const coverage = scoreCoverage(keywords, resumeText, els.vault.value);
  const hits = coverage.filter((item) => item.status === "hit").length;
  const partials = coverage.filter((item) => item.status === "partial").length;
  const misses = coverage.filter((item) => item.status === "miss").length;
  const bullets = sections.reduce((total, section) => total + section.lines.length, 0);
  const score = keywords.length ? Math.round(((hits + partials * 0.55) / keywords.length) * 100) : 0;
  const diff = buildResumeDiff(originalSections, sections);

  state.analysis = {
    originalSections,
    sections,
    resumeText,
    keywords,
    coverage,
    hits,
    partials,
    misses,
    bullets,
    score,
    diff,
  };
  renderAll();
}

function renderCompare() {
  const { diff } = state.analysis;
  const totalChanges = diff.added + diff.changed + diff.removed;
  const summary = `<div class="diff-summary" aria-label="Difference summary">
    <div><strong>${diff.added}</strong><span>added</span></div>
    <div><strong>${diff.changed}</strong><span>changed</span></div>
    <div><strong>${diff.removed}</strong><span>removed</span></div>
  </div>`;

  if (!totalChanges) {
    els.comparePreview.innerHTML = `${summary}<p class="empty">No differences detected against the original resume.</p>`;
    return;
  }

  els.comparePreview.innerHTML =
    summary +
    diff.sections
      .map((section) => {
        const rows = section.rows
          .map((row) => {
            if (row.type === "same") {
              return `<li class="diff-line same"><span class="diff-badge">Same</span><span>${escapeHtml(row.line)}</span></li>`;
            }
            if (row.type === "added") {
              return `<li class="diff-line added"><span class="diff-badge">Added</span><span><mark class="diff-token-added">${escapeHtml(row.line)}</mark></span></li>`;
            }
            return `<li class="diff-line changed">
              <div class="diff-current"><span class="diff-badge">Changed</span><span>${renderChangedText(row.previous, row.line)}</span></div>
              <div class="diff-before">Was: ${escapeHtml(row.previous)}</div>
            </li>`;
          })
          .join("");
        const removed = section.removed.length
          ? `<div class="diff-removed-block"><strong>Removed from original</strong><ul>${section.removed
              .map((line) => `<li>${escapeHtml(line)}</li>`)
              .join("")}</ul></div>`
          : "";
        return `<section class="diff-section"><h3>${escapeHtml(section.title)}</h3><ul class="diff-list">${rows}</ul>${removed}</section>`;
      })
      .join("");
}

function renderResume() {
  const { sections, bullets } = state.analysis;
  const { added, changed, removed } = state.analysis.diff;
  els.resumeStats.textContent = `${sections.length} sections · ${bullets} bullets · ${added + changed + removed} changes`;
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
  els.comparePreview.classList.toggle("hidden", state.previewMode !== "compare");
  els.resumePreview.classList.toggle("hidden", state.previewMode !== "resume");
  els.coveragePreview.classList.toggle("hidden", state.previewMode !== "coverage");
}

function renderAll() {
  renderCompare();
  renderResume();
  renderCoverage();
  renderScore();
  renderReview();
  renderPreviewMode();
}

function currentReportText() {
  const { score, hits, partials, misses, diff } = state.analysis;
  return [
    `Resume review: ${score}% coverage`,
    `Diff: ${diff.added} added, ${diff.changed} changed, ${diff.removed} removed`,
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

[els.jd, els.vault, els.original, els.latex].forEach((input) => {
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
