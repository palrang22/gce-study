// GCP Quiz 프론트엔드. 해시 라우팅: #/exam (시험 선택), #/ (범위 선택), #/quiz/{세션id} (풀이), #/wrong-notes (오답노트)
// 테스트 세션(문제 순서, 위치, 제출 결과)은 서버 DB에 저장된다.

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const app = $("#app");

const EXAM_KEY = "quiz.exam";             // 현재 고른 시험 ("ACE" | "PCA")
const NULL_SECTION = "null";
const EXAM_LABELS = { ACE: "Associate Cloud Engineer", PCA: "Professional Cloud Architect" };

const getExam = () => sessionStorage.getItem(EXAM_KEY) || "";
const setExam = (exam) => sessionStorage.setItem(EXAM_KEY, exam);
const homeKey = () => `quiz.home.${getExam()}`;          // 범위 선택 폼 값 (시험별)
const notesKey = () => `quiz.notesFilter.${getExam()}`;  // 오답노트 필터 (시험별)
const memosKey = () => `quiz.memosFilter.${getExam()}`;  // 메모 모아보기 필터 (시험별)

// ---------- 유틸 ----------

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// 지문/보기: 일반 텍스트 + ```코드 블록``` 만 지원 (HTML로 해석하지 않음)
function renderRich(text) {
  const parts = String(text ?? "").split(/```(\w*)\n([\s\S]*?)```/);
  let html = "";
  for (let i = 0; i < parts.length; i += 3) {
    const plain = parts[i].trim();
    if (plain) html += `<p>${escapeHtml(plain).replace(/\n/g, "<br>")}</p>`;
    if (i + 2 < parts.length) {
      html += `<pre><code>${escapeHtml(parts[i + 2].replace(/\n$/, ""))}</code></pre>`;
    }
  }
  return html;
}

// AI 해설: 마크다운. 원시 HTML은 글자 그대로 보여준다.
if (window.marked) {
  marked.use({ renderer: { html: (token) => escapeHtml(token.text ?? token) } });
}
// "**상태(Status)**를" 처럼 닫는 ** 바로 뒤에 한글이 붙으면 CommonMark 규칙상 굵게가 안 된다.
// 코드 블록 밖의 **...** (안에 `코드` 가능) 를 표시 문자로 바꿔 두고, 렌더 후 <strong> 으로 되돌린다.
const BOLD_OPEN = "";
const BOLD_CLOSE = "";
const BOLD_RE = /\*\*((?:[^*`\n]|`[^`\n]*`)+?)\*\*/g;
function protectBold(md) {
  return md
    .split(/(```[\s\S]*?```)/)
    .map((part, i) => (i % 2 ? part : part.replace(BOLD_RE, `${BOLD_OPEN}$1${BOLD_CLOSE}`)))
    .join("");
}
function renderMarkdown(md) {
  if (!window.marked) return `<pre>${escapeHtml(md)}</pre>`;
  return marked.parse(protectBold(md ?? ""))
    .replaceAll(BOLD_OPEN, "<strong>")
    .replaceAll(BOLD_CLOSE, "</strong>");
}

function loadJSON(key) {
  try { return JSON.parse(sessionStorage.getItem(key)); } catch { return null; }
}
function saveJSON(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* 저장 불가 환경 */ }
}

let toastTimer;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2500);
}

function debounce(fn, ms) {
  let t;
  const wrapped = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  wrapped.flush = (...args) => { clearTimeout(t); fn(...args); };
  return wrapped;
}

async function api(path, { method = "GET", body, params } = {}) {
  let url = path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "" || v === false) continue;
      if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
      else qs.append(k, v);
    }
    if (qs.toString()) url += "?" + qs;
  }
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data.detail) msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch { /* 본문 없음 */ }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

const sectionLabel = (s) => s ?? "섹션 없음";
const formatRate = (r) => (r == null ? "-" : `${Math.round(r * 100)}%`);
// "2026-10-06 14:22:05" → "2026-10-06 14:22"
const formatTime = (t) => (t ? t.slice(0, 16) : "");

function wrongBadge(count) {
  if (!count) return "";
  const level = count >= 3 ? "w3" : count === 2 ? "w2" : "w1";
  return `<span class="badge wrong-count ${level}">${count}회 틀림</span>`;
}

// ---------- 라우터 ----------

let renderId = 0;          // 비동기 렌더 중 화면이 바뀌면 이전 렌더 결과를 버린다
let cleanup = null;        // 화면별 전역 이벤트 해제

function route() {
  if (cleanup) { cleanup(); cleanup = null; }
  const hash = location.hash || "#/";
  const quiz = hash.match(/^#\/quiz(?:\/(\d+|notes))?/);
  const name = hash.startsWith("#/exam") ? "exam"
    : quiz ? "quiz"
    : hash.startsWith("#/wrong-notes") ? "wrong-notes"
    : hash.startsWith("#/memos") ? "memos"
    : "home";

  if (name !== "exam" && !getExam()) {
    location.hash = "#/exam";
    return;
  }

  $$(".topbar nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  $("#main-nav").hidden = name === "exam";
  $("#exam-switch").hidden = name === "exam";
  $("#brand").textContent = getExam() ? `GCP Quiz · ${getExam()}` : "GCP Quiz";

  const render =
    name === "exam" ? renderExamPicker
    : name === "quiz" ? () => renderQuiz(quiz[1] === "notes" ? "notes" : (quiz[1] ? Number(quiz[1]) : null))
    : name === "wrong-notes" ? renderWrongNotes
    : name === "memos" ? renderMemos
    : renderHome;
  render().catch((e) => {
    app.innerHTML = `<div class="card"><p>불러오지 못했어요: ${escapeHtml(e.message)}</p>
      <a href="#/">범위 선택으로 가기</a></div>`;
  });
}

// ---------- 시험 선택 ----------

async function renderExamPicker() {
  const rid = ++renderId;
  app.innerHTML = `<p class="muted">불러오는 중…</p>`;
  const exams = await api("/api/exams");
  if (rid !== renderId) return;

  window.scrollTo(0, 0);
  app.innerHTML = `
    <section class="card">
      <h1>어떤 시험을 공부할까요?</h1>
      <ul class="exam-list">
        ${exams.map((e) => `
          <li class="exam-item" data-exam="${escapeHtml(e.exam)}" tabindex="0">
            <div class="exam-title">${escapeHtml(e.exam)} <span class="muted">· ${escapeHtml(e.label)}</span></div>
            <div class="muted small">전체 ${e.total}문제 · 푼 문제 ${e.solved}${e.accuracy != null ? ` · 정답률 ${formatRate(e.accuracy)}` : ""}</div>
          </li>`).join("")}
      </ul>
    </section>`;

  const choose = (exam) => { setExam(exam); location.hash = "#/"; };
  for (const item of $$(".exam-item")) {
    item.addEventListener("click", () => choose(item.dataset.exam));
    item.addEventListener("keydown", (e) => { if (e.key === "Enter") choose(item.dataset.exam); });
  }
}

function openSession(id) {
  const target = `#/quiz/${id}`;
  if (location.hash === target) route();
  else location.hash = target;
}

async function startSession(ids, label, params = {}, startIndex = 0) {
  if (!ids.length) { toast("풀 문제가 없어요"); return; }
  try {
    const s = await api("/api/sessions", {
      method: "POST", body: { exam: getExam(), label, question_ids: ids, params },
    });
    if (startIndex) {
      await api(`/api/sessions/${s.id}`, { method: "PATCH", body: { current_index: startIndex } });
    }
    openSession(s.id);
  } catch (e) { toast(e.message); }
}

// ---------- 로컬 세션 (오답노트 연습: 서버에 세션을 안 만들고 브라우저에만 보관) ----------

const localSessionKey = () => `quiz.localSession.${getExam()}`;
const loadLocalSession = () => loadJSON(localSessionKey());
const saveLocalSession = (s) => saveJSON(localSessionKey(), s);

function startLocalSession(ids, numbers, label, startIndex = 0) {
  if (!ids.length) { toast("풀 문제가 없어요"); return; }
  saveLocalSession({ label, question_ids: ids, numbers, current_index: startIndex, results: {} });
  if (location.hash === "#/quiz/notes") route();
  else location.hash = "#/quiz/notes";
}

// ---------- 범위 선택 ----------

function sessionCard(s) {
  const done = s.current_index >= s.total;
  const progress = s.total ? (s.answered / s.total) * 100 : 0;
  return `
    <li class="session-item" data-id="${s.id}">
      <div class="session-main">
        <div class="session-title">${escapeHtml(s.label)}</div>
        <div class="muted small">
          ${s.answered} / ${s.total}문제 제출 · 정답 ${s.correct}개${s.answered ? ` (${formatRate(s.correct / s.answered)})` : ""}
          · ${done ? "완료" : `${s.current_index + 1}번째 문제에서 멈춤`}
        </div>
        <div class="bar thin"><span style="width:${progress}%"></span></div>
        <div class="muted small">시작 ${formatTime(s.created_at)} · 마지막 ${formatTime(s.updated_at)}</div>
      </div>
      <div class="session-actions">
        <button class="primary" data-action="open">${done ? "결과 보기" : "이어서 풀기"}</button>
        <button data-action="restart" title="같은 문제로 새 테스트">새로 시작</button>
        <button class="link danger" data-action="delete">삭제</button>
      </div>
    </li>`;
}

async function renderHome() {
  const rid = ++renderId;
  app.innerHTML = `<p class="muted">불러오는 중…</p>`;
  const exam = getExam();
  const [sections, stats, sessions] = await Promise.all([
    api("/api/sections", { params: { exam } }),
    api("/api/stats", { params: { exam } }),
    api("/api/sessions", { params: { exam } }),
  ]);
  if (rid !== renderId) return;

  const saved = loadJSON(homeKey()) || {};
  const o = stats.overall;
  const savedSections = saved.sections;
  const sectionValue = (s) => s.section ?? NULL_SECTION;

  app.innerHTML = `
    <section class="card">
      <h1>새 테스트</h1>
      <div class="stats-line">전체 ${o.total}문제 · 푼 문제 ${o.solved} · 누적 정답률 ${formatRate(o.accuracy)}</div>
      <form id="range-form">
        <fieldset>
          <legend>번호 구간</legend>
          <div class="row">
            <input type="number" name="from" min="1" max="${o.total}" placeholder="1" value="${escapeHtml(saved.from ?? "")}">
            <span>~</span>
            <input type="number" name="to" min="1" max="${o.total}" placeholder="${o.total}" value="${escapeHtml(saved.to ?? "")}">
          </div>
        </fieldset>
        <fieldset>
          <legend>섹션</legend>
          ${sections.map((s) => `
            <label class="choice">
              <input type="checkbox" name="section" value="${escapeHtml(sectionValue(s))}"
                ${!savedSections || savedSections.includes(sectionValue(s)) ? "checked" : ""}>
              ${escapeHtml(sectionLabel(s.section))} <span class="muted small">(${s.count})</span>
            </label>`).join("")}
        </fieldset>
        <fieldset>
          <legend>모드</legend>
          ${[["all", "전체"], ["wrong", "오답노트만"], ["unsolved", "안 푼 문제만"]].map(([v, label]) => `
            <label class="choice">
              <input type="radio" name="mode" value="${v}" ${(saved.mode ?? "all") === v ? "checked" : ""}> ${label}
            </label>`).join("")}
        </fieldset>
        <fieldset>
          <label class="choice"><input type="checkbox" name="shuffle" ${saved.shuffle ? "checked" : ""}> 랜덤 순서</label>
        </fieldset>
        <fieldset>
          <legend>테스트 이름 <span class="muted small">(비우면 자동)</span></legend>
          <input type="text" name="label" maxlength="60" class="wide" placeholder="">
        </fieldset>
        <div class="actions">
          <span class="grow preview-count" id="preview-count"></span>
          <button type="submit" class="primary" id="start">새 테스트 시작</button>
        </div>
      </form>
    </section>
    <section class="card">
      <h1>저장된 테스트 <span class="muted small">${sessions.length}개</span></h1>
      ${sessions.length
        ? `<ul class="session-list" id="session-list">${sessions.map(sessionCard).join("")}</ul>`
        : `<p class="muted">아직 없어요. 위에서 새 테스트를 시작하면 여기에 저장돼서 언제든 이어 풀 수 있어요.</p>`}
    </section>`;

  const form = $("#range-form");
  const readForm = () => ({
    from: form.from.value,
    to: form.to.value,
    sections: $$('input[name="section"]:checked', form).map((el) => el.value),
    mode: form.mode.value,
    shuffle: form.shuffle.checked,
  });
  const toParams = (f) => ({ exam, from: f.from, to: f.to, section: f.sections, mode: f.mode, shuffle: f.shuffle });
  const autoLabel = (f) => {
    const modeLabel = { all: "전체", wrong: "오답노트", unsolved: "안 푼 문제" }[f.mode];
    const range = f.from || f.to ? `${f.from || 1}~${f.to || o.total}번` : "전 범위";
    return `${range} · ${modeLabel}${f.shuffle ? " · 랜덤" : ""}`;
  };

  const updatePreview = debounce(async () => {
    const f = readForm();
    saveJSON(homeKey(), f);
    form.label.placeholder = autoLabel(f);
    const out = $("#preview-count");
    if (!out) return;
    if (!f.sections.length) {
      out.textContent = "섹션을 하나 이상 고르세요";
      $("#start").disabled = true;
      return;
    }
    try {
      const { count } = await api("/api/questions", { params: toParams(f) });
      out.textContent = `${count}문제`;
      $("#start").disabled = count === 0;
    } catch (e) {
      out.textContent = e.message;
    }
  }, 150);
  form.addEventListener("input", (e) => { if (e.target.name !== "label") updatePreview(); });
  updatePreview.flush();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = readForm();
    const { ids } = await api("/api/questions", { params: toParams(f) });
    const label = form.label.value.trim() || autoLabel(f);
    startSession(ids, label, { from: f.from, to: f.to, mode: f.mode, shuffle: f.shuffle });
  });

  $("#session-list")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const item = btn.closest(".session-item");
    const id = Number(item.dataset.id);
    const s = sessions.find((x) => x.id === id);
    if (btn.dataset.action === "open") openSession(id);
    if (btn.dataset.action === "restart") {
      const detail = await api(`/api/sessions/${id}`);
      startSession(detail.question_ids, s.label, s.params);
    }
    if (btn.dataset.action === "delete") {
      if (!confirm(`"${s.label}" 테스트를 삭제할까요? (풀이 기록과 오답노트는 남아요)`)) return;
      try {
        await api(`/api/sessions/${id}`, { method: "DELETE" });
        toast("삭제했어요");
        route();
      } catch (err) { toast(err.message); }
    }
  });
}

// ---------- 문제 풀이 ----------

async function renderQuiz(sessionId) {
  const rid = ++renderId;
  const isLocal = sessionId === "notes";  // 오답노트 연습: 서버 세션 없이 브라우저에만 보관 (localSessionKey)
  let session;
  if (isLocal) {
    session = loadLocalSession();
    if (!session) {
      app.innerHTML = `<div class="card"><p>오답노트 연습 정보를 찾을 수 없어요.</p><a href="#/wrong-notes">오답노트로 가기</a></div>`;
      return;
    }
  } else {
    if (sessionId == null) {
      // #/quiz 만 열면 가장 최근 테스트로
      const sessions = await api("/api/sessions", { params: { exam: getExam() } });
      if (rid !== renderId) return;
      if (!sessions.length) {
        app.innerHTML = `<div class="card"><p>저장된 테스트가 없어요.</p><a href="#/">새 테스트 시작하기</a></div>`;
        return;
      }
      history.replaceState(null, "", `#/quiz/${sessions[0].id}`);
      sessionId = sessions[0].id;
    }
    app.innerHTML = `<p class="muted">불러오는 중…</p>`;
    session = await api(`/api/sessions/${sessionId}`);
    if (rid !== renderId) return;
    if (session.current_index >= session.total) return renderFinished(session);
  }

  const total = session.question_ids.length;
  const qid = session.question_ids[session.current_index];
  if (isLocal) app.innerHTML = `<p class="muted">불러오는 중…</p>`;
  const q = await api(`/api/questions/${qid}`);
  if (rid !== renderId) return;

  const letters = Object.keys(q.options);
  const isLast = session.current_index + 1 === total;  // 오답노트 연습(local)은 마지막 문제에서 "다음" 비활성화, 결과 화면 없음
  let result = session.results[qid] || null;  // 이 테스트에서 이미 제출했으면 결과 유지
  let selected = new Set(result?.selected || []);
  let inNotes = q.in_wrong_notes;
  let wrongInfo = { wrong_count: q.wrong_count, last_wrong_at: q.last_wrong_at };

  window.scrollTo(0, 0);
  app.innerHTML = `
    <div class="progress">
      <div class="progress-text">
        <span>${escapeHtml(session.label)}</span>
        <form id="jump-form" class="jump-form">
          <input type="number" id="jump-input" min="1" placeholder="번호로 이동" aria-label="번호로 이동">
          <button type="submit">이동</button>
        </form>
        <span>${session.current_index + 1} / ${total}</span>
      </div>
      <div class="bar"><span style="width:${((session.current_index + 1) / total) * 100}%"></span></div>
    </div>
    <article class="card">
      <header class="q-head">
        <span class="q-num">Q${q.number}</span>
        ${q.section ? `<span class="badge">${escapeHtml(q.section)}</span>` : ""}
        ${q.multi ? `<span class="badge multi">${q.answer_count}개 선택</span>` : ""}
        <span id="wrong-info"></span>
        <span class="spacer"></span>
        <button class="note-toggle" id="note-toggle"></button>
      </header>
      <div class="q-text">${renderRich(q.question)}</div>
      ${q.images.length ? `
        <details class="figures">
          <summary>원본 그림 보기 (${q.images.length})</summary>
          ${q.images.map((src) => `<img src="/images/${src.split("/").map(encodeURIComponent).join("/")}" alt="Q${q.number} 원본 그림" loading="lazy">`).join("")}
        </details>` : ""}
      <div class="options" id="options" role="${q.multi ? "group" : "radiogroup"}">
        ${letters.map((l, i) => `
          <label class="option" data-letter="${l}">
            <input type="${q.multi ? "checkbox" : "radio"}" name="opt" value="${l}">
            <span class="key">${i + 1}</span>
            <span class="letter">${l}.</span>
            <div class="opt-text">${renderRich(q.options[l])}</div>
          </label>`).join("")}
      </div>
      ${q.multi ? `<p class="hint">정답 ${q.answer_count}개를 모두 골라야 정답이에요 (부분점수 없음)</p>` : ""}
      <div id="result"></div>
      <div class="actions" id="submit-row">
        <button class="primary" id="submit">제출<kbd>Enter</kbd></button>
      </div>
      <div class="ai-area" id="ai-area"></div>
      <div class="memo">
        <label for="memo">메모 <span class="memo-status" id="memo-status"></span></label>
        <textarea id="memo" rows="3" placeholder="헷갈린 점, 확인할 내용">${escapeHtml(q.memo)}</textarea>
      </div>
    </article>
    <nav class="pager">
      <button id="prev" ${session.current_index === 0 ? "disabled" : ""}>← 이전</button>
      <button id="next" ${isLast && isLocal ? "disabled" : ""}>${isLast ? (isLocal ? "다음" : "결과 보기") : "다음"} →</button>
    </nav>
    <p class="shortcuts">단축키: <kbd>1</kbd>~<kbd>${letters.length}</kbd> 보기 선택 · <kbd>Enter</kbd> 제출/다음 · <kbd>←</kbd><kbd>→</kbd> 이전/다음</p>`;

  const optionsEl = $("#options");

  function paintWrongInfo() {
    $("#wrong-info").innerHTML = wrongInfo.wrong_count
      ? `${wrongBadge(wrongInfo.wrong_count)} <span class="muted small">마지막 ${formatTime(wrongInfo.last_wrong_at)}</span>`
      : "";
  }

  // --- 선택 ---
  function paintSelection() {
    for (const label of $$(".option", optionsEl)) {
      const on = selected.has(label.dataset.letter);
      label.classList.toggle("selected", on && !result);
      $("input", label).checked = on;
    }
    $("#submit").disabled = !!result || selected.size === 0;
  }
  optionsEl.addEventListener("change", () => {
    if (result) return;
    selected = new Set($$("input:checked", optionsEl).map((el) => el.value));
    paintSelection();
  });
  function selectByIndex(i) {
    const l = letters[i];
    if (!l || result) return;
    if (q.multi) selected.has(l) ? selected.delete(l) : selected.add(l);
    else selected = new Set([l]);
    paintSelection();
  }

  // --- 오답노트 토글 ---
  function paintNote() {
    const btn = $("#note-toggle");
    btn.classList.toggle("on", inNotes);
    btn.textContent = inNotes ? "★ 오답노트" : "☆ 오답노트에 추가";
  }
  $("#note-toggle").addEventListener("click", async () => {
    try {
      const r = await api(`/api/wrong-notes/${qid}`, { method: "PUT", body: { active: !inNotes } });
      inNotes = r.active;
      paintNote();
      toast(inNotes ? "오답노트에 추가했어요" : "오답노트에서 뺐어요");
    } catch (e) { toast(e.message); }
  });

  // --- 제출과 결과 ---
  async function submit() {
    if (result || selected.size === 0) return;
    $("#submit").disabled = true;
    try {
      const r = await api(`/api/questions/${qid}/answer`, {
        method: "POST", body: { selected: [...selected], session_id: isLocal ? null : session.id },
      });
      result = r;
      inNotes = r.in_wrong_notes;
      wrongInfo = { wrong_count: r.wrong_count, last_wrong_at: r.last_wrong_at };
      if (isLocal) { session.results[qid] = r; saveLocalSession(session); }
      showResult();
    } catch (e) {
      toast(e.message);
      $("#submit").disabled = false;
    }
  }

  function explanationHtml(text) {
    if (!text) return `<span class="muted">원본 해설이 없어요.</span>`;
    if (/^https?:\/\//.test(text)) {
      return `<a href="${escapeHtml(text)}" target="_blank" rel="noopener noreferrer">examtopics 토론 보기 ↗</a>`;
    }
    return renderRich(text);
  }

  function showResult() {
    optionsEl.classList.add("locked");
    for (const label of $$(".option", optionsEl)) {
      const l = label.dataset.letter;
      label.classList.toggle("correct", result.answer.includes(l));
      label.classList.toggle("wrong", result.selected.includes(l) && !result.answer.includes(l));
      $("input", label).disabled = true;
    }
    paintSelection();
    paintNote();
    paintWrongInfo();
    $("#result").innerHTML = `
      <div class="result ${result.is_correct ? "ok" : "ng"}">
        ${result.is_correct ? "정답이에요!" : `오답이에요. 정답: ${result.answer.join(", ")}`}
        <span class="result-time">${formatTime(result.answered_at)}</span>
      </div>
      <details class="explanation" open>
        <summary>원본 해설</summary>
        <div>${explanationHtml(result.explanation)}</div>
      </details>`;
    $("#submit-row").hidden = true;
    loadSavedAi();
  }

  // --- AI 해설 ---
  const ai = { loading: false, data: null, error: null, messages: [], msgLoading: false, msgError: null };

  function aiMessageHtml(m) {
    return `
      <div class="ai-msg ${m.role}">
        <div class="ai-msg-role">${m.role === "user" ? "나" : "AI"}</div>
        <div class="ai-msg-content">${m.role === "model" ? renderMarkdown(m.content) : escapeHtml(m.content)}</div>
      </div>`;
  }

  function paintAi() {
    const area = $("#ai-area");
    if (!result) { area.innerHTML = ""; return; }
    const err = ai.error ? `<p class="ai-error">${escapeHtml(ai.error)}</p>` : "";
    if (!ai.data) {
      area.innerHTML = `
        <button id="ai-generate" ${ai.loading ? "disabled" : ""}>${ai.loading ? "AI 해설 생성 중…" : "AI 해설"}</button>${err}`;
      return;
    }
    const msgErr = ai.msgError ? `<p class="ai-error">${escapeHtml(ai.msgError)}</p>` : "";
    area.innerHTML = `
      <div class="ai-box">
        <div class="ai-head">
          <strong>AI 해설</strong>
          <span class="muted small">${escapeHtml(ai.data.model)} · ${escapeHtml(formatTime(ai.data.created_at))}</span>
          <span class="spacer"></span>
          <button class="link" id="ai-regenerate" ${ai.loading ? "disabled" : ""}>${ai.loading ? "생성 중…" : "다시 생성"}</button>
        </div>
        <div class="md">${renderMarkdown(ai.data.content)}</div>
        ${err}
        ${ai.messages.length ? `<div class="ai-messages">${ai.messages.map(aiMessageHtml).join("")}</div>` : ""}
        <form class="ai-ask" id="ai-ask-form">
          <input type="text" id="ai-ask-input" placeholder="이어서 질문하기" maxlength="2000" ${ai.msgLoading ? "disabled" : ""}>
          <button type="submit" ${ai.msgLoading ? "disabled" : ""}>${ai.msgLoading ? "답변 중…" : "질문"}</button>
        </form>
        ${msgErr}
      </div>`;
  }
  $("#ai-area").addEventListener("click", (e) => {
    if (e.target.closest("#ai-generate")) requestAi(false);
    if (e.target.closest("#ai-regenerate")) requestAi(true);
  });
  $("#ai-area").addEventListener("submit", (e) => {
    if (e.target.id === "ai-ask-form") { e.preventDefault(); askAi(); }
  });
  async function loadAiMessages() {
    try {
      ai.messages = await api(`/api/ai-explanations/${ai.data.id}/messages`);
    } catch { /* 못 불러오면 빈 대화로 시작 */ }
  }
  async function loadSavedAi() {
    try {
      ai.data = await api(`/api/questions/${qid}/ai-explain`, { params: { selected: result.selected.join(",") } });
      await loadAiMessages();
    } catch (e) {
      if (e.status !== 404) ai.error = e.message;
    }
    if (rid === renderId) paintAi();
  }
  async function requestAi(force) {
    if (ai.loading) return;
    ai.loading = true;
    ai.error = null;
    paintAi();
    try {
      ai.data = await api(`/api/questions/${qid}/ai-explain`, {
        method: "POST", params: { force }, body: { selected: result.selected },
      });
      ai.messages = [];
      await loadAiMessages();
    } catch (e) {
      ai.error = `AI 해설을 만들지 못했어요: ${e.message}`;
    } finally {
      ai.loading = false;
      if (rid === renderId) paintAi();
    }
  }
  async function askAi() {
    const input = $("#ai-ask-input");
    const content = input.value.trim();
    if (!content || ai.msgLoading) return;
    input.value = "";
    ai.msgLoading = true;
    ai.msgError = null;
    ai.messages = [...ai.messages, { role: "user", content, pending: true }];
    paintAi();
    try {
      const added = await api(`/api/ai-explanations/${ai.data.id}/messages`, {
        method: "POST", body: { content },
      });
      ai.messages = [...ai.messages.slice(0, -1), ...added];
    } catch (e) {
      // 질문은 서버에 이미 저장됐을 수 있어서 낙관적으로 추가한 말풍선은 그대로 둔다
      ai.msgError = `답변을 받지 못했어요: ${e.message}`;
    } finally {
      ai.msgLoading = false;
      if (rid === renderId) { paintAi(); $("#ai-ask-input")?.focus(); }
    }
  }

  // --- 메모 (입력 멈추면 자동 저장) ---
  const memoEl = $("#memo");
  let savedMemo = q.memo;
  const saveMemo = debounce(async () => {
    const memo = memoEl.value;
    if (memo === savedMemo) return;
    const status = $("#memo-status");
    status.textContent = "저장 중…";
    try {
      await api(`/api/wrong-notes/${qid}`, { method: "PUT", body: { memo } });
      savedMemo = memo;
      if (status.isConnected) status.textContent = "저장됨";
    } catch (e) {
      if (status.isConnected) status.textContent = "";
      toast(`메모 저장 실패: ${e.message}`);
    }
  }, 600);
  memoEl.addEventListener("input", () => { $("#memo-status").textContent = ""; saveMemo(); });
  memoEl.addEventListener("blur", () => saveMemo.flush());

  // --- 이동 ---
  // 서버 세션: 위치를 서버에 저장 → 새로고침/다른 날에도 이어서
  // 로컬(오답노트) 세션: 위치를 sessionStorage에 저장, 마지막 문제 다음은 없음 (결과 화면 X)
  let moving = false;
  async function go(delta) {
    const next = session.current_index + delta;
    if (next < 0 || moving) return;
    if (isLocal) {
      if (next >= total) return;
      session.current_index = next;
      saveLocalSession(session);
      route();
      return;
    }
    moving = true;
    try {
      await api(`/api/sessions/${session.id}`, { method: "PATCH", body: { current_index: next } });
      route();
    } catch (e) {
      toast(e.message);
      moving = false;
    }
  }
  $("#prev").addEventListener("click", () => go(-1));
  $("#next").addEventListener("click", () => go(1));

  // --- 번호로 이동 ---
  $("#jump-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#jump-input");
    const n = Number(input.value);
    if (!n) return;
    const idx = session.numbers.indexOf(n);
    if (idx === -1) { toast(`Q${n}은(는) 이 테스트 범위에 없어요`); return; }
    input.value = "";
    go(idx - session.current_index);
  });

  // --- 단축키 ---
  function onKey(e) {
    if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
    const t = e.target;
    if (t.matches("textarea, input[type=number], input[type=text], select")) return;
    if (/^[1-9]$/.test(e.key)) {
      e.preventDefault();
      selectByIndex(Number(e.key) - 1);
    } else if (e.key === "Enter") {
      if (t.matches("button, a, summary")) return;  // 포커스된 버튼의 기본 동작과 겹치지 않게
      e.preventDefault();
      result ? go(1) : submit();
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      go(1);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      go(-1);
    }
  }
  document.addEventListener("keydown", onKey);
  cleanup = () => {
    document.removeEventListener("keydown", onKey);
    saveMemo.flush();
  };

  $("#submit").addEventListener("click", submit);
  paintNote();
  paintWrongInfo();
  paintSelection();
  if (result) showResult();
}

function renderFinished(session) {
  const results = Object.values(session.results);
  const wrongIds = session.question_ids.filter((id) => session.results[id] && !session.results[id].is_correct);
  const unanswered = session.question_ids.filter((id) => !session.results[id]);
  window.scrollTo(0, 0);
  app.innerHTML = `
    <section class="card">
      <h1>테스트 결과</h1>
      <p class="muted">${escapeHtml(session.label)} · 시작 ${formatTime(session.created_at)}</p>
      <p>제출 ${results.length} / ${session.total}문제 · 정답 ${session.correct}개
        ${results.length ? `(${formatRate(session.correct / results.length)})` : ""}</p>
      <div class="actions wrap">
        <button id="back">← 마지막 문제로</button>
        <span class="grow"></span>
        <button id="retry-wrong" ${wrongIds.length ? "" : "disabled"}>틀린 ${wrongIds.length}문제로 새 테스트</button>
        <button id="retry-unanswered" ${unanswered.length ? "" : "disabled"}>안 푼 ${unanswered.length}문제로 새 테스트</button>
        <button id="restart">같은 문제로 새로 시작</button>
        <button class="primary" id="home">테스트 목록</button>
      </div>
    </section>`;
  const go = async (index) => {
    await api(`/api/sessions/${session.id}`, { method: "PATCH", body: { current_index: index } });
    route();
  };
  $("#back").addEventListener("click", () => go(session.total - 1));
  $("#retry-wrong").addEventListener("click", () => startSession(wrongIds, `${session.label} · 틀린 문제`));
  $("#retry-unanswered").addEventListener("click", () => startSession(unanswered, `${session.label} · 안 푼 문제`));
  $("#restart").addEventListener("click", () => startSession(session.question_ids, session.label, session.params));
  $("#home").addEventListener("click", () => { location.hash = "#/"; });
}

// ---------- 오답노트 ----------

async function renderWrongNotes() {
  const rid = ++renderId;
  app.innerHTML = `<p class="muted">불러오는 중…</p>`;
  const exam = getExam();
  const filter = { section: "", minWrong: 0, sort: "number", ...(loadJSON(notesKey()) || {}) };
  const [sections, notes] = await Promise.all([
    api("/api/sections", { params: { exam } }),
    api("/api/wrong-notes", {
      params: {
        exam,
        section: filter.section ? [filter.section] : undefined,
        min_wrong: filter.minWrong || undefined,
        sort: filter.sort,
      },
    }),
  ]);
  if (rid !== renderId) return;

  const ids = notes.map((n) => n.question_id);
  const countChips = [[0, "전체"], [1, "1회 이상"], [2, "2회 이상"], [3, "3회 이상"]];
  window.scrollTo(0, 0);
  app.innerHTML = `
    <section class="card">
      <div class="notes-head">
        <h1 class="grow">오답노트 <span class="muted small">${notes.length}문제</span></h1>
        <button class="primary" id="retry-all" ${notes.length ? "" : "disabled"}>이 목록으로 새 테스트</button>
      </div>
      <div class="notes-filters">
        <div class="chips" role="group" aria-label="틀린 횟수">
          ${countChips.map(([v, label]) => `
            <button class="chip ${filter.minWrong === v ? "on" : ""}" data-min="${v}">${label}</button>`).join("")}
        </div>
        <span class="grow"></span>
        ${sections.length > 1 ? `
          <select id="section-filter" aria-label="섹션 필터">
            <option value="">전체 섹션</option>
            ${sections.map((s) => {
              const v = s.section ?? NULL_SECTION;
              return `<option value="${escapeHtml(v)}" ${v === filter.section ? "selected" : ""}>${escapeHtml(sectionLabel(s.section))}</option>`;
            }).join("")}
          </select>` : ""}
        <select id="sort" aria-label="정렬">
          ${[["number", "번호순"], ["recent", "최근 틀린 순"], ["count", "많이 틀린 순"]].map(([v, label]) => `
            <option value="${v}" ${filter.sort === v ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </div>
      ${notes.length ? `
        <ul class="note-list">
          ${notes.map((n, i) => `
            <li class="note-item" data-index="${i}" tabindex="0">
              <span class="note-num">Q${n.number}</span>
              <span class="note-preview">${n.section ? `<span class="badge">${escapeHtml(n.section)}</span> ` : ""}${escapeHtml(n.question)}</span>
              <span class="note-count">${n.wrong_count ? wrongBadge(n.wrong_count) : `<span class="badge">직접 추가</span>`}</span>
              <div class="note-meta">
                ${n.wrong_count
                  ? `<details class="wrong-times">
                       <summary>마지막 오답 ${formatTime(n.last_wrong_at)}${n.wrong_count > 1 ? ` · 처음 ${formatTime(n.first_wrong_at)}` : ""}</summary>
                       <ol>${n.wrong_times.map((t) => `<li>${formatTime(t)}</li>`).join("")}</ol>
                     </details>`
                  : `<span>추가 ${formatTime(n.added_at)}</span>`}
                ${n.memo ? `<div class="note-memo">📝 ${escapeHtml(n.memo.length > 80 ? n.memo.slice(0, 80) + "…" : n.memo)}</div>` : ""}
              </div>
            </li>`).join("")}
        </ul>` : `<p class="muted">${filter.minWrong ? `${filter.minWrong}회 이상 틀린 문제가 없어요.` : "오답노트가 비어 있어요. 문제를 틀리면 자동으로 추가돼요."}</p>`}
    </section>`;

  const setFilter = (patch) => { saveJSON(notesKey(), { ...filter, ...patch }); route(); };
  for (const chip of $$(".chip")) {
    chip.addEventListener("click", () => setFilter({ minWrong: Number(chip.dataset.min) }));
  }
  $("#section-filter")?.addEventListener("change", (e) => setFilter({ section: e.target.value }));
  $("#sort").addEventListener("change", (e) => setFilter({ sort: e.target.value }));

  const listLabel = () => {
    const parts = ["오답노트"];
    if (filter.minWrong) parts.push(`${filter.minWrong}회 이상`);
    if (filter.section) parts.push(sectionLabel(filter.section === NULL_SECTION ? null : filter.section));
    return parts.join(" · ");
  };
  // 오답노트 연습은 서버에 세션을 만들지 않고 브라우저에만 임시로 들고 있는다 (startLocalSession)
  $("#retry-all").addEventListener("click", () => startLocalSession(ids, notes.map((n) => n.number), listLabel()));
  // 문제 클릭: 이 목록으로 연습을 시작하고 클릭한 문제에서 시작
  const open = (item) => startLocalSession(ids, notes.map((n) => n.number), listLabel(), Number(item.dataset.index));
  for (const item of $$(".note-item")) {
    item.addEventListener("click", (e) => { if (!e.target.closest("details")) open(item); });
    item.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === item) open(item); });
  }
}

// ---------- 메모 모아보기 (오답노트 active 여부와 무관하게, 메모 있는 문제 전부) ----------

async function renderMemos() {
  const rid = ++renderId;
  app.innerHTML = `<p class="muted">불러오는 중…</p>`;
  const exam = getExam();
  const filter = { section: "", sort: "number", ...(loadJSON(memosKey()) || {}) };
  const [sections, memos] = await Promise.all([
    api("/api/sections", { params: { exam } }),
    api("/api/memos", {
      params: {
        exam,
        section: filter.section ? [filter.section] : undefined,
        sort: filter.sort,
      },
    }),
  ]);
  if (rid !== renderId) return;

  const ids = memos.map((n) => n.question_id);
  window.scrollTo(0, 0);
  app.innerHTML = `
    <section class="card">
      <div class="notes-head">
        <h1 class="grow">메모 <span class="muted small">${memos.length}문제</span></h1>
        <button class="primary" id="retry-all" ${memos.length ? "" : "disabled"}>이 목록으로 보기</button>
      </div>
      <div class="notes-filters">
        <span class="grow"></span>
        ${sections.length > 1 ? `
          <select id="section-filter" aria-label="섹션 필터">
            <option value="">전체 섹션</option>
            ${sections.map((s) => {
              const v = s.section ?? NULL_SECTION;
              return `<option value="${escapeHtml(v)}" ${v === filter.section ? "selected" : ""}>${escapeHtml(sectionLabel(s.section))}</option>`;
            }).join("")}
          </select>` : ""}
        <select id="sort" aria-label="정렬">
          ${[["number", "번호순"], ["recent", "최근 수정 순"]].map(([v, label]) => `
            <option value="${v}" ${filter.sort === v ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </div>
      ${memos.length ? `
        <ul class="note-list">
          ${memos.map((n, i) => `
            <li class="note-item" data-index="${i}" tabindex="0">
              <span class="note-num">Q${n.number}</span>
              <span class="note-preview">${n.section ? `<span class="badge">${escapeHtml(n.section)}</span> ` : ""}${escapeHtml(n.question)}</span>
              <span class="note-count">${n.active ? `<span class="badge">오답노트</span>` : ""}</span>
              <div class="note-meta">
                <span>수정 ${formatTime(n.updated_at)}</span>
                <div class="note-memo">📝 ${escapeHtml(n.memo)}</div>
              </div>
            </li>`).join("")}
        </ul>` : `<p class="muted">메모가 비어 있어요. 문제 풀이 화면에서 메모를 적으면 여기 모여요.</p>`}
    </section>`;

  const setFilter = (patch) => { saveJSON(memosKey(), { ...filter, ...patch }); route(); };
  $("#section-filter")?.addEventListener("change", (e) => setFilter({ section: e.target.value }));
  $("#sort").addEventListener("change", (e) => setFilter({ sort: e.target.value }));

  const listLabel = () => {
    const parts = ["메모"];
    if (filter.section) parts.push(sectionLabel(filter.section === NULL_SECTION ? null : filter.section));
    return parts.join(" · ");
  };
  // 메모 모아보기도 오답노트 연습과 동일하게 서버 세션 없이 브라우저에만 임시로 들고 있는다 (startLocalSession)
  $("#retry-all").addEventListener("click", () => startLocalSession(ids, memos.map((n) => n.number), listLabel()));
  const open = (item) => startLocalSession(ids, memos.map((n) => n.number), listLabel(), Number(item.dataset.index));
  for (const item of $$(".note-item")) {
    item.addEventListener("click", (e) => { if (!e.target.closest("details")) open(item); });
    item.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === item) open(item); });
  }
}

$("#exam-switch").addEventListener("click", () => { location.hash = "#/exam"; });
window.addEventListener("hashchange", route);
route();
