/* ============================================================
   Expert finder — client-side intent detector.

   Pattern matches lib/duplication.ts: a pure, client-side router
   over the seeded trail (F + HIST) in lib/data.ts. Runs in ask()
   BEFORE callCheckDuplication. If the question isn't an
   expert-finder question, findExpert() returns null and the
   existing duplication flow is untouched.

   Three result shapes:
     - null                        → not an expert-finder question
     - { kind: "no_disease" }      → intent matched, no disease parsed
     - { kind: "unknown_disease" } → disease parsed but not in trail
     - { kind: "found", ... }      → ranked authors on that disease
   ============================================================ */

import { F, type Finding } from "./data";

/* ---- natural-language → canonical disease code ---------------- */
export const DISEASE_ALIASES: Record<string, string> = {
  bladder: "BLCA",
  "bladder cancer": "BLCA",
  urothelial: "BLCA",

  ovarian: "OV",
  hgsoc: "OV",
  "high-grade serous": "OV",
  "high grade serous": "OV",

  brain: "GBM",
  glioma: "GBM",
  glioblastoma: "GBM",

  lung: "LUAD",
  luad: "LUAD",
  adenocarcinoma: "LUAD",

  lymphoma: "DLBCL",
  dlbcl: "DLBCL",
  "diffuse large b-cell": "DLBCL",
  "diffuse large b cell": "DLBCL",

  mesothelioma: "MESO",
  meso: "MESO",

  "pan-cancer": "pan-cancer",
  pancancer: "pan-cancer",
  "pan cancer": "pan-cancer",
};

/* ---- initials → byline form ---------------------------------- */
/* We only store initials in the seed; keep them rather than invent
   first names. "Dr." is the safe professional form. */
export const AUTHOR_DISPLAY: Record<string, string> = {
  "A. Rahman": "Dr. A. Rahman",
  "D. Cho": "Dr. D. Cho",
  "P. Nair": "Dr. P. Nair",
  "M. Feld": "Dr. M. Feld",
  "L. Ortiz": "Dr. L. Ortiz",
  "S. Iqbal": "Dr. S. Iqbal",
};

const ALL_DISEASES = Array.from(new Set(F.map((f) => f.dis)));

/* ---- result types -------------------------------------------- */
export interface AuthorScore {
  name: string; // raw initials from F.au (e.g. "A. Rahman")
  display: string; // byline form (e.g. "Dr. A. Rahman")
  score: number;
  counts: { confirmed: number; in_progress: number; abandoned: number };
  findings: Finding[]; // the actual findings on this disease, status-desc order
}

export type ExpertResult =
  | { kind: "no_disease" }
  | { kind: "unknown_disease"; disease: string }
  | { kind: "found"; disease: string; authors: AuthorScore[] };

/* ---- intent regexes ------------------------------------------ */
const INTENT_A =
  /\b(who|find|looking for|connect me with|is there)\b.*\b(expert|specialist|lead|owner|pi|researcher|scientist|investigator|working on)\b/i;
const INTENT_B = /\bwho\b.*\b(work|works|working)\b.*\bon\b/i;

/* ---- status weights + recency tie-break ---------------------- */
const WEIGHT: Record<Finding["st"], number> = {
  confirmed: 3,
  in_progress: 2,
  abandoned: 1,
};
// lower = shown first when sorting tied findings within an author
const RECENCY_RANK: Record<Finding["st"], number> = {
  in_progress: 0,
  confirmed: 1,
  abandoned: 2,
};

/* ============================================================
   findExpert
   ============================================================ */
export function findExpert(text: string): ExpertResult | null {
  if (!INTENT_A.test(text) && !INTENT_B.test(text)) return null;

  const disease = parseDisease(text);
  if (!disease) return { kind: "no_disease" };
  if (!ALL_DISEASES.includes(disease)) return { kind: "unknown_disease", disease };

  const authors = scoreAuthors(disease);
  if (authors.length === 0) return { kind: "unknown_disease", disease };

  return { kind: "found", disease, authors };
}

/** Scan lowercased text for alias substrings and bare disease codes. */
function parseDisease(text: string): string | null {
  const t = ` ${text.toLowerCase()} `;

  // alias substrings first (longer phrases first so "bladder cancer"
  // beats bare "bladder" — though both map to BLCA anyway, the order
  // matters if we ever add overlapping aliases)
  const aliasKeys = Object.keys(DISEASE_ALIASES).sort((a, b) => b.length - a.length);
  for (const k of aliasKeys) {
    if (t.includes(k)) return DISEASE_ALIASES[k];
  }
  // bare code tokens (word-boundary match so "OV" doesn't hit "overlap")
  for (const d of ALL_DISEASES) {
    const code = d.split(" ")[0]; // "pan-cancer" → "pan-cancer", "GBM · OV" → "GBM"
    if (new RegExp(`\\b${escapeRe(code)}\\b`, "i").test(t)) return d;
  }
  return null;
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function scoreAuthors(disease: string): AuthorScore[] {
  const onDisease = F.filter((f) => f.dis === disease);
  const byAuthor = new Map<string, Finding[]>();
  onDisease.forEach((f) => {
    const list = byAuthor.get(f.au) ?? [];
    list.push(f);
    byAuthor.set(f.au, list);
  });

  const out: AuthorScore[] = [];
  byAuthor.forEach((findings, name) => {
    const counts = { confirmed: 0, in_progress: 0, abandoned: 0 };
    findings.forEach((f) => {
      counts[f.st]++;
    });
    const score =
      counts.confirmed * WEIGHT.confirmed +
      counts.in_progress * WEIGHT.in_progress +
      counts.abandoned * WEIGHT.abandoned;

    // status-desc within an author: in_progress first, then confirmed, abandoned
    const sortedFindings = [...findings].sort(
      (a, b) => RECENCY_RANK[a.st] - RECENCY_RANK[b.st],
    );

    out.push({
      name,
      display: AUTHOR_DISPLAY[name] ?? name,
      score,
      counts,
      findings: sortedFindings,
    });
  });

  // overall sort: score desc, then recency preference (in_progress > confirmed > abandoned)
  out.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return recencyPref(a.counts) - recencyPref(b.counts);
  });
  return out;
}

/** Lower is more recent-active. */
function recencyPref(c: { in_progress: number; confirmed: number; abandoned: number }): number {
  if (c.in_progress > 0) return 0;
  if (c.confirmed > 0) return 1;
  return 2;
}

/* ============================================================
   Markdown render — three branches
   ============================================================ */
export function buildExpertMarkdown(_text: string, result: ExpertResult): string {
  if (result.kind === "no_disease") {
    return [
      `## Which disease?`,
      ``,
      `Breadcrumbs can name the right consult when you name the disease — try`,
      `**DLBCL, BLCA, OV (ovarian / HGSOC), GBM (brain / glioma), MESO, LUAD (lung),**`,
      `or **pan-cancer**.`,
      ``,
      `For example: *"who is the DLBCL expert at Owkin?"*`,
      ``,
    ].join("\n");
  }

  if (result.kind === "unknown_disease") {
    const pretty = prettyDisease(result.disease);
    const neighbors = adjacentDiseases();
    const hint = neighbors.length
      ? `Or rephrase with a disease your org works on: ${neighbors.join(", ")}.`
      : `Rephrase with a disease your org works on (DLBCL, BLCA, OV, GBM, MESO, LUAD).`;
    return [
      `## No expert on ${pretty} at Owkin — yet`,
      ``,
      `Nothing on your org's trail touches ${pretty}. Either this is genuinely new ground`,
      `for the team, or it has been explored under a related name.`,
      ``,
      `### What Breadcrumbs can do next`,
      `- Drop a marker so the **next** person asking about ${pretty} finds *you*`,
      `- Check the published record for prior work worth anchoring to`,
      neighbors.length
        ? `- Surface adjacent trails — try asking "${adjacentSuggestion(neighbors[0])}"`
        : `- Surface adjacent trails with a related disease name`,
      ``,
      `---`,
      ``,
      hint,
      ``,
    ].join("\n");
  }

  // found
  const { disease, authors } = result;

  if (authors.length === 1) return singleExpertMarkdown(disease, authors[0]);
  return multiExpertMarkdown(disease, authors);
}

function singleExpertMarkdown(disease: string, a: AuthorScore): string {
  const pretty = prettyDisease(disease);
  const lines: string[] = [];
  lines.push(`## Owkin expert on ${disease}`);
  lines.push(``);

  const summary = findingSummary(a.findings[0]);
  lines.push(
    `**${a.display}** is the most active investigator on ${disease} (${pretty}) across`,
    `your organization's research trail — ${summary.lead}.`,
  );
  lines.push(``);
  lines.push(`### Why this name surfaced`);
  lines.push(`- ${countsBullet(a)} — prior work has shipped`);
  // show the most recent / top finding question
  lines.push(`- **Recent question**: "${a.findings[0].q}"`);
  lines.push(``);
  lines.push(`---`);
  lines.push(``);
  lines.push(
    `${a.display.split(" ").slice(-1)[0]} is the natural first consult for this disease.`,
    `If your question overlaps with this ground, reach out before running a new`,
    `hypothesis — they may already have the answer, or a reason it isn't worth re-running.`,
  );
  lines.push(``);
  return lines.join("\n");
}

function multiExpertMarkdown(disease: string, authors: AuthorScore[]): string {
  const pretty = prettyDisease(disease);
  const lines: string[] = [];
  lines.push(`## Owkin experts on ${disease}`);
  lines.push(``);
  lines.push(
    `${authors.length} names come up on your org's trail for ${pretty} (${disease}):`,
  );
  lines.push(``);

  authors.forEach((a, i) => {
    const isLead = i === 0;
    const heading = isLead
      ? `${a.display} — lead`
      : `${a.display} — also working on this`;
    lines.push(`### ${heading}`);
    lines.push(`- ${countsBullet(a)}`);
    // one-line consult hint derived from their top finding's category
    const topF = a.findings[0];
    lines.push(`- Natural consult for: ${consultHint(topF)}`);
    lines.push(``);
  });

  lines.push(`---`);
  lines.push(``);
  // consult routing sentence
  const lead = authors[0];
  const tail = authors.slice(1);
  lines.push(
    `For ${consultHint(lead.findings[0])}, ${lead.display} is the right first message.`,
  );
  tail.forEach((a) => {
    const last = a.display.split(" ").slice(-1)[0];
    if (a.counts.abandoned > 0 && a.counts.confirmed === 0 && a.counts.in_progress === 0) {
      lines.push(
        `For treatment-response work in ${disease}, Dr. ${last} carries hard-won context`,
        `— including why a path here is closed (see abandoned findings below).`,
      );
    } else {
      lines.push(
        `Dr. ${last} carries direct, hard-won context on ${consultHint(a.findings[0])}.`,
      );
    }
  });
  lines.push(`Breadcrumbs can surface any of their full trails on request.`);
  lines.push(``);
  return lines.join("\n");
}

/* ---- markdown helpers ---------------------------------------- */

/** "1 confirmed finding" / "2 confirmed findings and 1 abandoned attempt" */
function countsBullet(a: AuthorScore): string {
  const parts: string[] = [];
  if (a.counts.confirmed > 0) {
    parts.push(
      `**${a.counts.confirmed} confirmed finding${a.counts.confirmed > 1 ? "s" : ""}**`,
    );
  }
  if (a.counts.in_progress > 0) {
    parts.push(
      `**${a.counts.in_progress} in-progress**`,
    );
  }
  if (a.counts.abandoned > 0) {
    parts.push(
      `**${a.counts.abandoned} abandoned** attempt${a.counts.abandoned > 1 ? "s" : ""}`,
    );
  }
  if (parts.length === 0) return `**0 findings**`;
  return parts.join(" and ");
}

/** Pull a short consult-angle hint from the finding's category. */
function consultHint(f: Finding): string {
  const cat = f.cat.toLowerCase();
  if (cat.includes("mutation")) return `mutation-driven spatial phenotypes`;
  if (cat.includes("treatment") || cat.includes("response")) return `treatment-response questions`;
  if (cat.includes("myeloid")) return `myeloid / TME composition questions`;
  if (cat.includes("hypoxia")) return `hypoxia co-localization`;
  if (cat.includes("h&e")) return `H&E → spatial bridging`;
  if (cat.includes("cxcl13")) return `CXCL13-driven niches`;
  if (cat.includes("sypl1")) return `SYPL1 spatial localization`;
  if (cat.includes("exhaustion")) return `T cell exhaustion markers`;
  if (cat.includes("pan-cancer")) return `pan-cancer recurring programs`;
  if (cat.includes("heterogeneity")) return `intra-tumor heterogeneity`;
  return `this disease area`;
}

function findingSummary(f: Finding): { lead: string } {
  // one-line natural-language summary of the most recent finding
  if (!f) return { lead: `no findings on record` };
  const verb =
    f.st === "confirmed"
      ? `one confirmed finding`
      : f.st === "in_progress"
        ? `one in-progress investigation`
        : `one abandoned attempt`;
  const topic = f.ent.length ? f.ent.join(" + ") : f.cat.toLowerCase();
  return {
    lead: `${verb} on ${topic} in the MOSAIC cohort`,
  };
}

const PRETTY_DISEASE: Record<string, string> = {
  DLBCL: "diffuse large B-cell lymphoma",
  OV: "ovarian cancer",
  BLCA: "bladder cancer",
  GBM: "glioblastoma",
  MESO: "mesothelioma",
  LUAD: "lung adenocarcinoma",
  "pan-cancer": "pan-cancer",
};

function prettyDisease(code: string): string {
  return PRETTY_DISEASE[code] ?? code.toLowerCase();
}

/** Diseases the org actually works on, for the "try this instead" hint. */
function adjacentDiseases(): string[] {
  return ["DLBCL", "BLCA", "OV", "GBM", "MESO", "LUAD"];
}

function adjacentSuggestion(disease: string): string {
  const pretty = prettyDisease(disease).toLowerCase();
  return `who is the ${disease} expert at Owkin?" or "what's been explored on ${pretty}?"`;
}
