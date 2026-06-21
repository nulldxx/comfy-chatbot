import {
  escapeHtml, parseJsonResponse, expandAliases, applyReplacements,
  buildVideoPrompt, isVideoUrl, fmtDuration, clampVideo, recomputeVideo,
  deriveFaceDetailPrompt, DEFAULT_VIDEO_SETTINGS, VIDEO_LIMITS,
} from './utils.js';
import { state, DEFAULT_DENOISE, RESOLUTION_PRESETS } from './state.js';
import { messagesEl, sendBtn, addMessage, scrollBottom, deleteImageFile, removeImageFromChat } from './dom.js';
import { createSlideshow } from './slideshow.js';
import { renderReviewGrid, renderCompositeGrid, renderSequenceReview } from './grids.js';

function renderWorkflowPicker({ url, title, loadingText, failLabel, emptyMsg, current, setMsg, onSelect }) {
  const bubble = addMessage('bot', `<div class="status-text">${loadingText}</div>`).parentElement.querySelector('.bubble');
  fetch(url).then(r => r.json()).then(workflows => {
    if (!workflows.length && emptyMsg) {
      bubble.innerHTML = emptyMsg;
      return;
    }
    let html = `<strong>${title}</strong><div class="sel-list">`;
    workflows.forEach(wf => {
      const isCur = wf === current;
      html += `<button class="sel-btn${isCur ? ' current' : ''}" data-wf="${escapeHtml(wf)}">
                 ${escapeHtml(wf)}${isCur ? ' <span style="color:#7c3aed">✓</span>' : ''}
               </button>`;
    });
    html += '</div>';
    bubble.innerHTML = html;
    bubble.querySelectorAll('.sel-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const wf = btn.dataset.wf;
        onSelect(wf);
        bubble.innerHTML = `${setMsg} <strong style="color:#a78bfa">${escapeHtml(wf)}</strong>`;
      });
    });
    scrollBottom();
  }).catch(() => { bubble.innerHTML = `<span style="color:#f87171">Failed to load ${failLabel}.</span>`; });
}

function renderSessionPicker({ headerHtml, onSelect }) {
  const bubble = addMessage('bot', '<div class="status-text">Loading saved sessions…</div>').parentElement.querySelector('.bubble');
  fetch('/api/sessions')
  .then(parseJsonResponse)
  .then(sessions => {
    if (!sessions.length) {
      bubble.innerHTML = 'No saved sessions yet. Use <code>/session-save &lt;name&gt;</code> to save one.';
      scrollBottom();
      return;
    }
    const list = document.createElement('div');
    list.innerHTML = headerHtml;
    const selList = document.createElement('div');
    selList.className = 'sel-list';

    sessions.forEach(s => {
      const date = s.saved_at ? new Date(s.saved_at).toLocaleDateString() : '';
      const row = document.createElement('div');
      row.className = 'sel-row';

      const btn = document.createElement('button');
      btn.className = 'sel-btn';
      btn.dataset.name = s.name;
      btn.innerHTML = `<span>${escapeHtml(s.name)}</span>
        <span style="color:#475569;font-size:0.8em">${s.image_count} image(s)${date ? ' · ' + date : ''}</span>`;
      btn.addEventListener('click', () => onSelect(btn.dataset.name, bubble));

      const delBtn = document.createElement('button');
      delBtn.className = 'sel-del-btn';
      delBtn.title = 'Delete session';
      delBtn.innerHTML = '🗑';
      delBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const name = btn.dataset.name;
        delBtn.disabled = true;
        delBtn.style.opacity = '0.4';
        fetch('/api/sessions/' + encodeURIComponent(name), { method: 'DELETE' })
        .then(parseJsonResponse)
        .then(data => {
          if (data.error) throw new Error(data.error);
          row.remove();
          if (!selList.querySelector('.sel-row')) {
            bubble.innerHTML = 'No saved sessions yet. Use <code>/session-save &lt;name&gt;</code> to save one.';
          }
          scrollBottom();
        })
        .catch(err => {
          delBtn.disabled = false;
          delBtn.style.opacity = '';
          addMessage('bot', `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`);
          scrollBottom();
        });
      });

      row.appendChild(btn);
      row.appendChild(delBtn);
      selList.appendChild(row);
    });

    list.appendChild(selList);
    bubble.innerHTML = '';
    bubble.appendChild(list);
    scrollBottom();
  })
  .catch(() => {
    bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load sessions.</span>';
    scrollBottom();
  });
}

function showSessionSummary() {
  const rows = [];

  const srvName  = state.currentServer ? state.currentServer.name    : DEFAULT_SERVER;
  const srvAddr  = state.currentServer ? state.currentServer.address : DEFAULT_SERVER;
  const srvOs    = state.currentServer ? state.currentServer.os      : DEFAULT_SERVER_OS;
  const srvLabel = state.currentServer
    ? `<span style="color:#a78bfa">${escapeHtml(srvName)}</span> <span style="color:#475569">(${escapeHtml(srvAddr)}, ${escapeHtml(srvOs)})</span>`
    : `<span style="color:#a78bfa">${escapeHtml(srvName)}</span> <span style="color:#475569">(default)</span>`;
  rows.push({ label: 'Server', value: srvLabel });

  const wfActive = state.currentWorkflow || DEFAULT_WORKFLOW;
  const wfLabel  = state.currentWorkflow
    ? `<span style="color:#a78bfa">${escapeHtml(wfActive)}</span>`
    : `<span style="color:#a78bfa">${escapeHtml(wfActive)}</span> <span style="color:#475569">(default)</span>`;
  rows.push({ label: 'Workflow', value: wfLabel });

  const faceWfActive = state.currentFaceWorkflow || DEFAULT_FACE_WORKFLOW;
  const faceWfLabel  = faceWfActive
    ? (state.currentFaceWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(faceWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(faceWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Face-detail workflow', value: faceWfLabel });

  const upWfActive = state.currentUpscaleWorkflow || DEFAULT_UPSCALE_WORKFLOW;
  const upWfLabel  = upWfActive
    ? (state.currentUpscaleWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(upWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(upWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Upscale workflow', value: upWfLabel });

  const i2iWfActive = state.currentImage2ImageWorkflow || DEFAULT_IMAGE2IMAGE_WORKFLOW;
  const i2iWfLabel  = i2iWfActive
    ? (state.currentImage2ImageWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(i2iWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(i2iWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Image2image workflow', value: i2iWfLabel });

  const i2vWfActive = state.currentImage2VideoWorkflow || DEFAULT_IMAGE2VIDEO_WORKFLOW;
  const i2vWfLabel  = i2vWfActive
    ? (state.currentImage2VideoWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(i2vWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(i2vWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Image2video workflow', value: i2vWfLabel });

  const inpaintWfActive = state.currentInpaintingWorkflow || DEFAULT_INPAINTING_WORKFLOW;
  const inpaintWfLabel  = inpaintWfActive
    ? (state.currentInpaintingWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(inpaintWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(inpaintWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Inpainting workflow', value: inpaintWfLabel });

  const resLabel = state.currentResolution
    ? `<span style="color:#a78bfa">${state.currentResolution.width}×${state.currentResolution.height}</span>`
    : `<span style="color:#475569">workflow default</span>`;
  rows.push({ label: 'Resolution', value: resLabel });

  rows.push({ label: 'Iterations', value: `<span style="color:#a78bfa">${state.iterations}</span>${state.iterations > 1 ? ' per prompt' : ''}` });

  if (state.currentGenerationSteps !== null) {
    rows.push({ label: 'Generation steps', value: `<span style="color:#a78bfa">${state.currentGenerationSteps}</span>` });
  }

  const DENOISE_LABELS = { face: 'Face-detailer', image2image: 'Image2image', inpaint: 'Inpainting', upscale: 'Upscale' };
  const denoiseOverrides = Object.entries(state.currentDenoise)
    .filter(([k, v]) => v !== DEFAULT_DENOISE[k])
    .map(([k, v]) => `${DENOISE_LABELS[k]}: <span style="color:#a78bfa">${v.toFixed(2)}</span>`)
    .join(' · ');
  if (denoiseOverrides) {
    rows.push({ label: 'Denoise overrides', value: denoiseOverrides });
  }

  const vs = state.currentVideoSettings;
  rows.push({
    label: 'Video settings',
    value: `<span style="color:#a78bfa">${fmtDuration(vs.duration)}s</span> · ` +
           `<span style="color:#a78bfa">${vs.frames}</span> frames · ` +
           `<span style="color:#a78bfa">${vs.fps}</span> fps · ` +
           `<span style="color:#a78bfa">${vs.width}×${vs.height}</span> · ` +
           `audio <span style="color:#a78bfa">${vs.audio !== false ? 'on' : 'off'}</span> ` +
           `<span style="color:#475569">(🔒 ${state.videoLock})</span>`,
  });

  if (state.lastFaceDetailPrompt) {
    rows.push({ label: 'Face-detail prompt', value: `<code>${escapeHtml(state.lastFaceDetailPrompt)}</code>` });
  }

  if (state.lastInpaintingPrompt) {
    rows.push({ label: 'Inpainting prompt', value: `<code>${escapeHtml(state.lastInpaintingPrompt)}</code>` });
  }

  if (state.sequenceReplacements.length) {
    const list = state.sequenceReplacements
      .map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`)
      .join(', ');
    rows.push({ label: `Sequence replacements (${state.sequenceReplacements.length})`, value: list });
  }

  if (state.image2imageReplacements.length) {
    const list = state.image2imageReplacements
      .map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`)
      .join(', ');
    rows.push({ label: `Image2image replacements (${state.image2imageReplacements.length})`, value: list });
  }

  if (state.image2imageOverridePrompt) {
    rows.push({ label: 'Image2image override prompt', value: `<code>${escapeHtml(state.image2imageOverridePrompt)}</code>` });
  }

  if (state.image2videoReplacements.length) {
    const list = state.image2videoReplacements
      .map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`)
      .join(', ');
    rows.push({ label: `Image2video replacements (${state.image2videoReplacements.length})`, value: list });
  }

  if (state.image2videoOverridePrompt) {
    rows.push({ label: 'Image2video override prompt', value: `<code>${escapeHtml(state.image2videoOverridePrompt)}</code>` });
  }

  const aliasKeys = Object.keys(state.ALIASES).sort();
  if (aliasKeys.length) {
    const preview = aliasKeys.slice(0, 3).map(k => `<code>${escapeHtml(k)}</code>`).join(', ');
    const more = aliasKeys.length > 3 ? ` <span style="color:#475569">+${aliasKeys.length - 3} more</span>` : '';
    rows.push({ label: `Aliases (${aliasKeys.length})`, value: `${preview}${more} — <code>/alias-list</code> to see all` });
  }

  rows.push({ label: 'Session images', value: `<span style="color:#a78bfa">${state.sessionImages.length}</span>` });

  const rowsHtml = rows
    .map(r => `<div style="font-size:0.85rem;color:#94a3b8"><strong style="color:#cbd5e1">${r.label}:</strong> ${r.value}</div>`)
    .join('');
  addMessage('bot', `<strong>Session summary</strong><div class="sel-list" style="margin-top:10px;gap:4px">${rowsHtml}</div>`);
  scrollBottom();
}

// ---------------------------------------------------------------------------
// /jobs — server-side generation job tracker
// ---------------------------------------------------------------------------
//
// Generation threads run independently of the SSE connection that streams their
// progress; if the browser disconnects (phone loses signal, tab closed) the
// thread keeps running and writes the asset into IMAGES_DIR, but the user has
// no obvious way to find it later. /jobs hits GET /api/jobs (last 10 ComfyUI
// jobs newest-first) and renders a card for each with status, asset preview,
// and buttons to cancel/dismiss the job or pull a completed asset back into
// the current chat as a normal bot message.

const JOB_STATUS_LABELS = {
  pending:   { label: 'Queued',      color: '#94a3b8' },
  running:   { label: 'In progress', color: '#38bdf8' },
  done:      { label: 'Done',        color: '#22c55e' },
  error:     { label: 'Failed',      color: '#f87171' },
  cancelled: { label: 'Cancelled',   color: '#94a3b8' },
};

function _fmtRelative(ts) {
  if (!ts) return '';
  const diff = (Date.now() / 1000) - ts;
  if (diff < 60) return `${Math.max(0, Math.round(diff))}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

function _isJobActive(job) {
  return job.status === 'pending' || job.status === 'running';
}

function renderJobsGrid(bubble, deps) {
  let refreshTimer = null;

  function stopAutoRefresh() {
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
  }

  function scheduleAutoRefresh(jobs) {
    stopAutoRefresh();
    if (jobs.some(_isJobActive)) {
      refreshTimer = setTimeout(load, 5000);
    }
  }

  function load() {
    fetch('/api/jobs')
      .then(r => r.json())
      .then(jobs => {
        if (!Array.isArray(jobs)) throw new Error('Unexpected response');
        if (!jobs.length) {
          bubble.innerHTML = 'No tracked jobs — generate something first!';
          return;
        }
        bubble.innerHTML = '';
        const grid = document.createElement('div');
        grid.className = 'jobs-grid';
        jobs.forEach(job => grid.appendChild(buildJobCard(job)));
        bubble.appendChild(grid);
        scheduleAutoRefresh(jobs);
        scrollBottom();
      })
      .catch(err => {
        stopAutoRefresh();
        bubble.innerHTML = `<span style="color:#f87171">⚠ Failed to load jobs: ${escapeHtml(err.message || err)}</span>`;
      });
  }

  function buildJobCard(job) {
    const card = document.createElement('div');
    card.className = 'job-card';

    const statusInfo = JOB_STATUS_LABELS[job.status] || { label: job.status, color: '#94a3b8' };
    const headerHtml = `
      <div class="job-card-header">
        <span class="job-status-badge" style="background:${statusInfo.color}1f;color:${statusInfo.color};border:1px solid ${statusInfo.color}55">
          ${escapeHtml(statusInfo.label)}
        </span>
        <span class="job-card-kind" style="color:#94a3b8;font-size:0.78rem">${escapeHtml(job.kind || '')}</span>
        <span class="job-card-time" style="color:#475569;font-size:0.75rem;margin-left:auto">${escapeHtml(_fmtRelative(job.started_at))}</span>
      </div>
    `;

    let previewHtml = '';
    const firstAsset = (job.assets || [])[0];
    if (firstAsset) {
      if (isVideoUrl(firstAsset)) {
        previewHtml = `<video class="job-card-preview" src="${escapeHtml(firstAsset)}" muted preload="metadata" playsinline></video>`;
      } else {
        previewHtml = `<img class="job-card-preview" src="${escapeHtml(firstAsset)}" alt="">`;
      }
    } else {
      previewHtml = `<div class="job-card-preview job-card-preview-empty"><div class="dots"><span></span><span></span><span></span></div></div>`;
    }

    const promptText = (job.summary || job.prompt || '(no prompt)');
    const truncated = promptText.length > 120 ? promptText.slice(0, 117) + '…' : promptText;
    const assetsHtml = (job.assets || []).length
      ? `<div class="job-card-assets">${(job.assets || []).map(a => `<code>${escapeHtml(a)}</code>`).join('<br>')}</div>`
      : '';
    const errorHtml = job.error
      ? `<div class="job-card-error" style="color:#f87171;font-size:0.78rem;margin-top:4px">⚠ ${escapeHtml(job.error)}</div>`
      : '';

    card.innerHTML = `
      ${headerHtml}
      ${previewHtml}
      <div class="job-card-summary" style="font-size:0.82rem;color:#cbd5e1;margin-top:6px">${escapeHtml(truncated)}</div>
      ${assetsHtml}
      ${errorHtml}
      <div class="job-card-actions" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px"></div>
    `;

    const actions = card.querySelector('.job-card-actions');

    if (_isJobActive(job)) {
      const cancelBtn = document.createElement('button');
      cancelBtn.className = 'sel-btn';
      cancelBtn.textContent = '✕ Cancel';
      cancelBtn.addEventListener('click', () => {
        cancelBtn.disabled = true;
        fetch('/api/cancel/' + encodeURIComponent(job.job_id), { method: 'POST' })
          .finally(() => setTimeout(load, 500));
      });
      actions.appendChild(cancelBtn);
    } else {
      const dismissBtn = document.createElement('button');
      dismissBtn.className = 'sel-btn';
      dismissBtn.textContent = '✕ Dismiss';
      dismissBtn.title = 'Remove from this list (does not delete the asset)';
      dismissBtn.addEventListener('click', () => {
        dismissBtn.disabled = true;
        fetch('/api/jobs/' + encodeURIComponent(job.job_id), { method: 'DELETE' })
          .finally(load);
      });
      actions.appendChild(dismissBtn);
    }

    if (job.status === 'done' && (job.assets || []).length) {
      const pullBtn = document.createElement('button');
      pullBtn.className = 'sel-btn';
      pullBtn.textContent = '⬇ Pull into chat';
      pullBtn.title = 'Insert the asset(s) as a new bot message in this chat';
      pullBtn.addEventListener('click', () => {
        pullBtn.disabled = true;
        pullAssetsIntoChat(job, deps);
      });
      actions.appendChild(pullBtn);
    }

    return card;
  }

  function pullAssetsIntoChat(job, deps) {
    const promptLine = job.prompt
      ? `<div style="color:#94a3b8;font-size:0.85rem;margin-bottom:6px">From job: <code>${escapeHtml(job.summary || job.prompt)}</code></div>`
      : '';
    const wrap = addMessage('bot', promptLine || '<div style="color:#94a3b8;font-size:0.85rem;margin-bottom:6px">Pulled in from /jobs</div>');
    (job.assets || []).forEach(url => {
      if (job.prompt) state.imagePrompts[url] = job.prompt;
      // Avoid double-adding if the user is pulling the same asset twice.
      if (state.sessionImages.indexOf(url) === -1) {
        state.sessionImages.push(url);
      }
      if (typeof deps.appendChatImage === 'function') {
        deps.appendChatImage(wrap, url);
      }
    });
    scrollBottom();
  }

  load();
}

export function makeCommandHandler(deps) {
  function handleSlashCommand(raw) {
    const parts = raw.trim().split(/\s+/);
    const cmd   = parts[0].toLowerCase();

    const gridRunners = {
      runFaceDetail:  deps.runFaceDetail,
      runUpscale:     deps.runUpscale,
      runImage2Image: deps.runImage2Image,
      runDoOver:      deps.runDoOver,
      runImage2Video: deps.runImage2Video,
      runInpaint:     deps.runInpaint,
    };

    if (cmd === '/multi-prompt') {
      const lines = raw.slice('/multi-prompt'.length).split('\n').map(l => l.trim()).filter(Boolean);
      if (!lines.length) {
        addMessage('user', escapeHtml(raw), null);
        addMessage('bot', '<span style="color:#f87171">⚠ Paste line-separated prompts after <code>/multi-prompt</code> (use Shift+Enter between lines)</span>');
        return;
      }
      state.iterationsFromSequence = false;
      sendBtn.disabled = true;
      (async () => {
        for (const prompt of lines) {
          const expanded = expandAliases(prompt, state.ALIASES);
          addMessage('user', escapeHtml(expanded), expanded);
          const ok = await deps.runGeneration(expanded, '');
          if (!ok) break;
        }
        sendBtn.disabled = false;
      })();
      return;
    }

    if (cmd === '/sequence') {
      const master = expandAliases(raw.slice('/sequence'.length).trim(), state.ALIASES);
      addMessage('user', escapeHtml(raw), raw);
      if (!master) {
        addMessage('bot', '<span style="color:#f87171">⚠ Provide a master prompt, e.g. <code>/sequence a woman practising yoga at sunrise</code></span>');
        return;
      }
      const count = state.iterations === 1 ? 15 : state.iterations;
      state.iterationsFromSequence = true;
      sendBtn.disabled = true;
      const statusBubble = addMessage('bot', `
        <div class="status-text">Asking Grok for ${count} prompt(s)…</div>
        <div class="dots"><span></span><span></span><span></span></div>
      `);
      (async () => {
        const result = await deps.runSequenceJob('/api/sequence', master, count, statusBubble);
        if (!result) { sendBtn.disabled = false; return; }
        const prompts = result.prompts || [];
        state.lastSequence = { video: false, items: prompts.map(p => ({ prompt: p, action: '', audio: '' })) };
        statusBubble.innerHTML = `<div class="status-text">Grok returned <strong style="color:#a78bfa">${prompts.length}</strong> prompt(s) — generating one after another…</div>`;
        scrollBottom();
        for (const prompt of prompts) {
          addMessage('user', escapeHtml(prompt), prompt);
          const ok = await deps.runGeneration(prompt, '');
          if (!ok) break;
        }
        sendBtn.disabled = false;
      })();
      return;
    }

    if (cmd === '/video-sequence') {
      const master = expandAliases(raw.slice('/video-sequence'.length).trim(), state.ALIASES);
      addMessage('user', escapeHtml(raw), raw);
      if (!master) {
        addMessage('bot', '<span style="color:#f87171">⚠ Provide a master prompt, e.g. <code>/video-sequence a woman dancing in the rain</code></span>');
        return;
      }
      const count = state.iterations === 1 ? 15 : state.iterations;
      state.iterationsFromSequence = true;
      sendBtn.disabled = true;
      const statusBubble = addMessage('bot', `
        <div class="status-text">Asking Grok for ${count} video shot(s)…</div>
        <div class="dots"><span></span><span></span><span></span></div>
      `);
      (async () => {
        const result = await deps.runSequenceJob('/api/video-sequence', master, count, statusBubble);
        if (!result) { sendBtn.disabled = false; return; }
        const shots = result.prompts || [];
        state.lastSequence = {
          video: true,
          items: shots.map(s => ({ prompt: s.prompt || '', action: s.action || '', audio: s.audio || '' })),
        };
        statusBubble.innerHTML = `<div class="status-text">Grok returned <strong style="color:#a78bfa">${shots.length}</strong> shot(s) — generating one after another…</div>`;
        scrollBottom();
        for (const shot of shots) {
          const prompt = shot.prompt || '';
          if (!prompt) continue;
          addMessage('user', escapeHtml(prompt), prompt);
          const ok = await deps.runGeneration(prompt, '', null, {
            videoMeta: { action: shot.action || '', audio: shot.audio || '' },
          });
          if (!ok) break;
        }
        sendBtn.disabled = false;
      })();
      return;
    }

    if (cmd === '/sequence-review') {
      addMessage('user', escapeHtml(raw), raw);
      if (!state.lastSequence || !state.lastSequence.items.length) {
        addMessage('bot', '<span style="color:#f87171">⚠ No sequence has been run yet — use <code>/sequence</code> or <code>/video-sequence</code> first.</span>');
        return;
      }
      const bubble = addMessage('bot', '');
      renderSequenceReview(bubble, state.lastSequence, { runGeneration: deps.runGeneration });
      return;
    }

    if (cmd === '/sequence-replacement-reset') {
      addMessage('user', escapeHtml(raw), raw);
      state.sequenceReplacements = [];
      addMessage('bot', 'Sequence replacements cleared.');
      return;
    }

    if (cmd === '/sequence-replacement') {
      addMessage('user', escapeHtml(raw), raw);
      if (!parts[1]) {
        if (!state.sequenceReplacements.length) {
          addMessage('bot', `No sequence replacements set.<br>Usage: <code>/sequence-replacement &lt;from&gt; &lt;to&gt;</code> — the first word is the text to find, the rest is what to replace it with. Matching is case-insensitive and preserves the matched case (<code>bird</code>→<code>dog</code>, <code>Bird</code>→<code>Dog</code>). Applied to every prompt <code>/sequence</code> gets back from Grok.<br><code>/sequence-replacement-reset</code> removes them all.`);
        } else {
          const list = state.sequenceReplacements.map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`).join('<br>');
          addMessage('bot', `<strong>Sequence replacements:</strong><br>${list}<br><br><code>/sequence-replacement-reset</code> removes them all.`);
        }
        return;
      }
      const from = parts[1];
      const to   = parts.slice(2).join(' ');
      if (!to) {
        addMessage('bot', '<span style="color:#f87171">⚠ Provide both a from and a to value, e.g. <code>/sequence-replacement woman elegant woman in a red dress</code></span>');
        return;
      }
      state.sequenceReplacements.push([from, to]);
      addMessage('bot', `Replacement added: <code>${escapeHtml(from)}</code> → <code>${escapeHtml(to)}</code>. Applied to every prompt from <code>/sequence</code>.`);
      return;
    }

    if (cmd === '/image2image-replacement') {
      addMessage('user', escapeHtml(raw), raw);
      if (!parts[1]) {
        if (!state.image2imageReplacements.length) {
          addMessage('bot', `No image2image replacements set.<br>Usage: <code>/image2image-replacement &lt;from&gt; &lt;to&gt;</code> — the first word is the text to find, the rest is what to replace it with. Applied to the original generation prompt when <code>/image2image</code> is run with no prompt.<br><code>/image2image-replacement-reset</code> removes them all.`);
        } else {
          const list = state.image2imageReplacements.map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`).join('<br>');
          addMessage('bot', `<strong>Image2image replacements:</strong><br>${list}<br><br><code>/image2image-replacement-reset</code> removes them all.`);
        }
        return;
      }
      const from = parts[1];
      const to   = parts.slice(2).join(' ');
      if (!to) {
        addMessage('bot', '<span style="color:#f87171">⚠ Provide both a from and a to value, e.g. <code>/image2image-replacement Dog Cat</code></span>');
        return;
      }
      state.image2imageReplacements.push([from, to]);
      addMessage('bot', `Replacement added: <code>${escapeHtml(from)}</code> → <code>${escapeHtml(to)}</code>. Applied to the original generation prompt when <code>/image2image</code> runs with no prompt.`);
      return;
    }

    if (cmd === '/image2image-replacement-reset') {
      addMessage('user', escapeHtml(raw), raw);
      state.image2imageReplacements = [];
      addMessage('bot', 'Image2image replacements cleared.');
      return;
    }

    if (cmd === '/image2image-set-prompt') {
      addMessage('user', escapeHtml(raw), raw);
      const override = raw.slice('/image2image-set-prompt'.length).trim();
      if (!override) {
        if (state.image2imageOverridePrompt) {
          addMessage('bot', `Current image2image override prompt: <code>${escapeHtml(state.image2imageOverridePrompt)}</code><br>Usage: <code>/image2image-set-prompt &lt;prompt&gt;</code> — overrides the per-image original prompt when <code>/image2image</code> (or the 🎨 button) runs without its own prompt. <code>/image2image-set-prompt-reset</code> clears it.`);
        } else {
          addMessage('bot', 'No image2image override prompt set.<br>Usage: <code>/image2image-set-prompt &lt;prompt&gt;</code> — overrides the per-image original prompt when <code>/image2image</code> (or the 🎨 button) runs without its own prompt. Useful after a <code>/review</code> when the original prompts aren\'t available.');
        }
        return;
      }
      state.image2imageOverridePrompt = override;
      addMessage('bot', `Image2image override prompt set: <code>${escapeHtml(override)}</code>. It will be used by <code>/image2image</code> and the 🎨 button until cleared with <code>/image2image-set-prompt-reset</code>.`);
      return;
    }

    if (cmd === '/image2image-set-prompt-reset') {
      addMessage('user', escapeHtml(raw), raw);
      state.image2imageOverridePrompt = null;
      addMessage('bot', 'Image2image override prompt cleared.');
      return;
    }

    if (cmd === '/image2image-workflow') {
      renderWorkflowPicker({
        url: '/api/image2image-workflows',
        title: 'Select an image2image workflow:',
        loadingText: 'Loading image2image workflows…',
        failLabel: 'image2image workflows',
        emptyMsg: 'No image2image workflows available — add one to the <code>image2image/</code> folder.',
        current: state.currentImage2ImageWorkflow || DEFAULT_IMAGE2IMAGE_WORKFLOW,
        setMsg: 'Image2image workflow set to',
        onSelect: wf => { state.currentImage2ImageWorkflow = wf; },
      });
      return;
    }

    if (cmd === '/image2video-replacement') {
      addMessage('user', escapeHtml(raw), raw);
      if (!parts[1]) {
        if (!state.image2videoReplacements.length) {
          addMessage('bot', `No image2video replacements set.<br>Usage: <code>/image2video-replacement &lt;from&gt; &lt;to&gt;</code> — the first word is the text to find, the rest is what to replace it with. Applied to the original generation prompt when <code>/image2video</code> is run with no prompt.<br><code>/image2video-replacement-reset</code> removes them all.`);
        } else {
          const list = state.image2videoReplacements.map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`).join('<br>');
          addMessage('bot', `<strong>Image2video replacements:</strong><br>${list}<br><br><code>/image2video-replacement-reset</code> removes them all.`);
        }
        return;
      }
      const from = parts[1];
      const to   = parts.slice(2).join(' ');
      if (!to) {
        addMessage('bot', '<span style="color:#f87171">⚠ Provide both a from and a to value, e.g. <code>/image2video-replacement Dog Cat</code></span>');
        return;
      }
      state.image2videoReplacements.push([from, to]);
      addMessage('bot', `Replacement added: <code>${escapeHtml(from)}</code> → <code>${escapeHtml(to)}</code>. Applied to the original generation prompt when <code>/image2video</code> runs with no prompt.`);
      return;
    }

    if (cmd === '/image2video-replacement-reset') {
      addMessage('user', escapeHtml(raw), raw);
      state.image2videoReplacements = [];
      addMessage('bot', 'Image2video replacements cleared.');
      return;
    }

    if (cmd === '/image2video-set-prompt') {
      addMessage('user', escapeHtml(raw), raw);
      const override = raw.slice('/image2video-set-prompt'.length).trim();
      if (!override) {
        if (state.image2videoOverridePrompt) {
          addMessage('bot', `Current image2video override prompt: <code>${escapeHtml(state.image2videoOverridePrompt)}</code><br>Usage: <code>/image2video-set-prompt &lt;prompt&gt;</code> — overrides the per-image original prompt when <code>/image2video</code> (or the 🎬 button) runs without its own prompt. <code>/image2video-set-prompt-reset</code> clears it.`);
        } else {
          addMessage('bot', 'No image2video override prompt set.<br>Usage: <code>/image2video-set-prompt &lt;prompt&gt;</code> — overrides the per-image original prompt when <code>/image2video</code> (or the 🎬 button) runs without its own prompt. Useful after a <code>/review</code> when the original prompts aren\'t available.');
        }
        return;
      }
      state.image2videoOverridePrompt = override;
      addMessage('bot', `Image2video override prompt set: <code>${escapeHtml(override)}</code>. It will be used by <code>/image2video</code> and the 🎬 button until cleared with <code>/image2video-set-prompt-reset</code>.`);
      return;
    }

    if (cmd === '/image2video-set-prompt-reset') {
      addMessage('user', escapeHtml(raw), raw);
      state.image2videoOverridePrompt = null;
      addMessage('bot', 'Image2video override prompt cleared.');
      return;
    }

    if (cmd === '/image2video-workflow') {
      renderWorkflowPicker({
        url: '/api/image2video-workflows',
        title: 'Select an image2video workflow:',
        loadingText: 'Loading image2video workflows…',
        failLabel: 'image2video workflows',
        emptyMsg: 'No image2video workflows available — add one to the <code>image2video/</code> folder.',
        current: state.currentImage2VideoWorkflow || DEFAULT_IMAGE2VIDEO_WORKFLOW,
        setMsg: 'Image2video workflow set to',
        onSelect: wf => { state.currentImage2VideoWorkflow = wf; },
      });
      return;
    }

    if (cmd === '/image2video') {
      addMessage('user', escapeHtml(raw), raw);
      if (!state.sessionImages.length) {
        addMessage('bot', 'No image from this session for image2video — generate one first.');
        return;
      }
      const i2vArg = raw.slice('/image2video'.length).trim();
      if (i2vArg !== '' && !/^\d+$/.test(i2vArg)) {
        addMessage('bot', '<span style="color:#f87171">⚠ <code>/image2video</code> takes only a number (how many recent images to process). To use a custom prompt, set one with <code>/image2video-set-prompt &lt;prompt&gt;</code> first.</span>');
        return;
      }
      const i2vN = i2vArg !== '' ? parseInt(i2vArg, 10) : 1;
      if (i2vN < 1) {
        addMessage('bot', '<span style="color:#f87171">⚠ Usage: <code>/image2video</code> or <code>/image2video &lt;N&gt;</code></span>');
        return;
      }
      const i2vTargets = state.sessionImages.slice(-i2vN);
      let i2vChain = Promise.resolve();
      let i2vAborted = false;
      i2vTargets.forEach(img => {
        i2vChain = i2vChain.then(() => {
          if (i2vAborted) return;
          let prompt;
          if (state.image2videoOverridePrompt) {
            prompt = state.image2videoOverridePrompt;
          } else {
            const orig = state.imagePrompts[img];
            if (!orig) {
              i2vAborted = true;
              addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2video-set-prompt &lt;prompt&gt;</code></span>');
              return;
            }
            prompt = buildVideoPrompt(applyReplacements(orig, state.image2videoReplacements), state.imageVideoMeta[img], state.currentVideoSettings.audio);
          }
          addMessage('user', 'Image2video: ' + escapeHtml(prompt), prompt);
          return deps.runImage2Video(prompt, img);
        });
      });
      return;
    }

    if (cmd === '/inpaint-workflow') {
      renderWorkflowPicker({
        url: '/api/inpainting-workflows',
        title: 'Select an inpainting workflow:',
        loadingText: 'Loading inpainting workflows…',
        failLabel: 'inpainting workflows',
        emptyMsg: 'No inpainting workflows available — add one to the <code>inpainting/</code> folder.',
        current: state.currentInpaintingWorkflow || DEFAULT_INPAINTING_WORKFLOW,
        setMsg: 'Inpainting workflow set to',
        onSelect: wf => { state.currentInpaintingWorkflow = wf; },
      });
      return;
    }

    if (cmd === '/inpainting-prompt') {
      const prompt = raw.slice('/inpainting-prompt'.length).trim();
      addMessage('user', escapeHtml(raw), raw);
      if (!prompt) {
        state.lastInpaintingPrompt = null;
        addMessage('bot', 'Inpainting prompt cleared — the 🩹 button will show an error until a new one is set.');
        return;
      }
      state.lastInpaintingPrompt = prompt;
      addMessage('bot', `Inpainting prompt set — the 🩹 button will use <code>${escapeHtml(prompt)}</code>.`);
      return;
    }

    if (cmd === '/image2image') {
      addMessage('user', escapeHtml(raw), raw);
      if (!state.sessionImages.length) {
        addMessage('bot', 'No image from this session for image2image — generate one first.');
        return;
      }
      const i2iArg = raw.slice('/image2image'.length).trim();
      if (i2iArg !== '' && !/^\d+$/.test(i2iArg)) {
        addMessage('bot', '<span style="color:#f87171">⚠ <code>/image2image</code> takes only a number (how many recent images to process). To use a custom prompt, set one with <code>/image2image-set-prompt &lt;prompt&gt;</code> first.</span>');
        return;
      }
      const i2iN = i2iArg !== '' ? parseInt(i2iArg, 10) : 1;
      if (i2iN < 1) {
        addMessage('bot', '<span style="color:#f87171">⚠ Usage: <code>/image2image</code> or <code>/image2image &lt;N&gt;</code></span>');
        return;
      }
      const i2iTargets = state.sessionImages.slice(-i2iN);
      let i2iChain = Promise.resolve();
      let i2iAborted = false;
      i2iTargets.forEach(img => {
        i2iChain = i2iChain.then(() => {
          if (i2iAborted) return;
          let prompt;
          if (state.image2imageOverridePrompt) {
            prompt = state.image2imageOverridePrompt;
          } else {
            const orig = state.imagePrompts[img];
            if (!orig) {
              i2iAborted = true;
              addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2image-set-prompt &lt;prompt&gt;</code></span>');
              return;
            }
            prompt = applyReplacements(orig, state.image2imageReplacements);
          }
          addMessage('user', 'Image2image: ' + escapeHtml(prompt), prompt);
          return deps.runImage2Image(prompt, img);
        });
      });
      return;
    }

    if (cmd === '/workflow-iterate') {
      const master = raw.slice('/workflow-iterate'.length).trim();
      addMessage('user', escapeHtml(raw), raw);
      if (!master) {
        addMessage('bot', '<span style="color:#f87171">⚠ Provide a prompt, e.g. <code>/workflow-iterate a cat astronaut</code> — then tick the workflows to run it against</span>');
        return;
      }
      const bubble = addMessage('bot', '<div class="status-text">Loading workflows…</div>').parentElement.querySelector('.bubble');
      fetch('/api/workflows').then(r => r.json()).then(workflows => {
        if (!workflows.length) {
          bubble.innerHTML = 'No workflows available.';
          return;
        }
        let html = `<strong>Run against which workflows?</strong>
          <div style="font-size:0.8rem;color:#94a3b8;margin:4px 0 8px">Prompt: <code>${escapeHtml(master)}</code></div>
          <div class="sel-list">`;
        workflows.forEach((wf, i) => {
          html += `<label class="wfi-row" style="display:flex;align-items:center;gap:8px;font-size:0.9rem;color:#cbd5e1;cursor:pointer">
                     <input type="checkbox" class="wfi-check" value="${escapeHtml(wf)}" id="wfi-${i}">
                     ${escapeHtml(wf)}
                   </label>`;
        });
        html += `</div>
          <button class="sel-btn wfi-go" style="margin-top:10px">Generate</button>`;
        bubble.innerHTML = html;

        const goBtn = bubble.querySelector('.wfi-go');
        goBtn.addEventListener('click', () => {
          const selected = [...bubble.querySelectorAll('.wfi-check:checked')].map(c => c.value);
          if (!selected.length) {
            if (!bubble.querySelector('.wfi-warn')) {
              const warn = document.createElement('div');
              warn.className = 'wfi-warn';
              warn.style.cssText = 'color:#f87171;font-size:0.82rem;margin-top:6px';
              warn.textContent = '⚠ Tick at least one workflow.';
              bubble.appendChild(warn);
            }
            return;
          }
          bubble.querySelectorAll('.wfi-check, .wfi-go').forEach(el => { el.disabled = true; });
          const warn = bubble.querySelector('.wfi-warn');
          if (warn) warn.remove();
          bubble.insertAdjacentHTML('beforeend',
            `<div class="status-text" style="margin-top:8px">Generating <strong style="color:#a78bfa">${selected.length}</strong> workflow(s)…</div>`);

          state.iterationsFromSequence = false;
          sendBtn.disabled = true;
          (async () => {
            for (let i = 0; i < selected.length; i++) {
              const wf = selected[i];
              const label = ` — ${wf} (${i + 1}/${selected.length})`;
              const ok = await deps.runGeneration(master, label, wf);
              if (!ok) break;
            }
            sendBtn.disabled = false;
          })();
        });
        scrollBottom();
      }).catch(() => { bubble.innerHTML = '<span style="color:#f87171">Failed to load workflows.</span>'; });
      return;
    }

    if (cmd === '/face-detail-prompt') {
      const prompt = raw.slice('/face-detail-prompt'.length).trim();
      addMessage('user', escapeHtml(raw), raw);
      if (!prompt) {
        addMessage('bot', '<span style="color:#f87171">⚠ Provide a prompt, e.g. <code>/face-detail-prompt a clear, detailed face &lt;lora:name:strength&gt;</code></span>');
        return;
      }
      if (!/<lora:[^>]+>/i.test(prompt)) {
        addMessage('bot', '<span style="color:#f87171">⚠ <code>/face-detail-prompt</code> needs a LoRA tag in the prompt, e.g. <code>/face-detail-prompt a clear, detailed face &lt;lora:name:strength&gt;</code> — type <code>/lora</code> to find one.</span>');
        return;
      }
      state.lastFaceDetailPrompt = prompt;
      addMessage('bot', `Face-detail prompt set — the face icons will use <code>${escapeHtml(prompt)}</code>.`);
      return;
    }

    if (cmd === '/face-detail-prompt-reset') {
      addMessage('user', escapeHtml(raw), raw);
      state.lastFaceDetailPrompt = null;
      addMessage('bot', 'Face-detail prompt cleared — the face icons will derive a prompt from each image again.');
      return;
    }

    if (cmd === '/upscale') {
      addMessage('user', escapeHtml(raw), raw);
      if (!state.sessionImages.length) {
        addMessage('bot', 'No image from this session to upscale — generate one first.');
        return;
      }
      const upscaleArg = raw.slice('/upscale'.length).trim();
      const upscaleN = upscaleArg ? parseInt(upscaleArg, 10) : 1;
      if (isNaN(upscaleN) || upscaleN < 1) {
        addMessage('bot', '<span style="color:#f87171">⚠ Usage: <code>/upscale</code> or <code>/upscale &lt;N&gt;</code> — upscale the last N images</span>');
        return;
      }
      const upscaleTargets = state.sessionImages.slice(-upscaleN);
      let upscaleChain = Promise.resolve();
      upscaleTargets.forEach(img => { upscaleChain = upscaleChain.then(() => deps.runUpscale(img)); });
      return;
    }

    if (cmd === '/face-detail-session') {
      addMessage('user', escapeHtml(raw), raw);
      if (!state.sessionImages.length) {
        addMessage('bot', 'No images from this session to face-detail — generate some first.');
        return;
      }
      const fdSessionTargets = state.sessionImages.slice();
      let fdSessionChain = Promise.resolve();
      fdSessionTargets.forEach(img => {
        fdSessionChain = fdSessionChain.then(() => {
          const prompt = state.lastFaceDetailPrompt || deriveFaceDetailPrompt(state.imagePrompts[img]);
          if (!prompt) {
            addMessage('bot', '<span style="color:#f87171">No LoRA in this image’s prompt — set one with <code>/face-detail-prompt &lt;prompt&gt;</code></span>');
            return;
          }
          addMessage('user', 'Face detail: ' + escapeHtml(prompt));
          return deps.runFaceDetail(prompt, img);
        });
      });
      return;
    }

    if (cmd === '/face-detail') {
      addMessage('user', escapeHtml(raw), raw);
      if (!state.sessionImages.length) {
        addMessage('bot', 'No image from this session to face-detail — generate one first.');
        return;
      }
      const fdArg = raw.slice('/face-detail'.length).trim();
      const fdN = fdArg ? parseInt(fdArg, 10) : 1;
      if (isNaN(fdN) || fdN < 1) {
        addMessage('bot', '<span style="color:#f87171">⚠ Usage: <code>/face-detail</code> or <code>/face-detail &lt;N&gt;</code> — face-detail the last N images</span>');
        return;
      }
      const fdTargets = state.sessionImages.slice(-fdN);
      let fdChain = Promise.resolve();
      fdTargets.forEach(img => {
        fdChain = fdChain.then(() => {
          const prompt = state.lastFaceDetailPrompt || deriveFaceDetailPrompt(state.imagePrompts[img]);
          if (!prompt) {
            addMessage('bot', '<span style="color:#f87171">No LoRA in this image’s prompt — set one with <code>/face-detail-prompt &lt;prompt&gt;</code></span>');
            return;
          }
          addMessage('user', 'Face detail: ' + escapeHtml(prompt));
          return deps.runFaceDetail(prompt, img);
        });
      });
      return;
    }

    addMessage('user', escapeHtml(raw), raw);

    if (cmd === '/help') {
      const filter   = raw.slice('/help'.length).trim();
      const filterLc = filter.toLowerCase();

      const helpEntries = [
        { sig: '/addserver <name> <host:port:os>', desc: 'add a server', notes: 'OS types: <code>unix</code> (Linux/macOS) &nbsp;·&nbsp; <code>windows</code> (Windows path separators)<br>e.g. <code>/addserver mordor mordor:8000:windows</code><br>e.g. <code>/addserver mybox 192.168.1.50:8188:unix</code>' },
        { sig: '/alias-create <word> <expansion>', desc: 'create or update a text alias; typing the word in a prompt and pressing space expands it immediately', notes: 'e.g. <code>/alias-create prophoto "Professional Photo, Medium format look"</code> &nbsp;·&nbsp; quotes are optional' },
        { sig: '/alias-list', desc: 'list all defined aliases' },
        { sig: '/archive-all [name]', desc: 'archive every image and video in the output folder into the encrypted volume (asks y/n first; optional folder name)', notes: 'needs the <code>archive-agent</code> running on the host and <code>ARCHIVE_*</code> set on the server' },
        { sig: '/archive-session [name]', desc: 'copy this session\'s images and videos into the encrypted volume, then remove the originals (optional folder name, e.g. <code>/archive-session man walking on beach</code>)' },
        { sig: '/archive-today [name]', desc: 'archive images and videos generated today into the encrypted volume (optional folder name)' },
        { sig: '/clear', desc: 'clear the visible chat while keeping settings, prompt history (up-arrow recall) and session images (<code>/review-session</code>)' },
        { sig: '/delete', desc: 'delete the last image' },
        { sig: '/delete-all', desc: 'delete every image in the output folder (asks y/n first)' },
        { sig: '/delete-session', desc: 'delete all images from this session (chat + output folder)' },
        { sig: '/delete-today', desc: 'delete every image generated today (asks y/n first)' },
        { sig: '/face-detail [N]', desc: 'run face-detail over the last N images (default 1); uses <code>/face-detail-prompt</code> override or derives from each image\'s prompt' },
        { sig: '/face-detail-prompt <prompt>', desc: 'set the prompt the per-image face (&#128100;) icons use; otherwise each icon derives one from that image\'s own prompt (needs a <code>&lt;lora:…&gt;</code> tag)' },
        { sig: '/face-detail-prompt-reset', desc: 'clear that override so the face icons derive a prompt from each image again' },
        { sig: '/face-detail-session', desc: 'face-detail every image from this session, one after another' },
        { sig: '/face-detail-workflow', desc: 'choose which face-detailer workflow the face icons use' },
        { sig: '/help', desc: 'show this message' },
        { sig: '/image2image [N]', desc: 're-run an image2image workflow over the last N images (default 1), each from its own original prompt, or the override prompt if set' },
        { sig: '/image2image-replacement <from> <to>', desc: 'find→replace applied to the original prompt when <code>/image2image</code> runs with no override (no args lists them)' },
        { sig: '/image2image-replacement-reset', desc: 'clear all image2image replacements' },
        { sig: '/image2image-set-prompt <prompt>', desc: 'override prompt used by <code>/image2image</code> and the 🎨 button instead of each image\'s original prompt (handy after a <code>/review</code>); no args shows it' },
        { sig: '/image2image-set-prompt-reset', desc: 'clear the override prompt' },
        { sig: '/image2image-workflow', desc: 'choose which image2image workflow <code>/image2image</code> uses' },
        { sig: '/image2video [N]', desc: 'run an image2video workflow over the last N images (default 1), each from its own original prompt or the override prompt if set' },
        { sig: '/image2video-replacement <from> <to>', desc: 'find→replace applied to the original prompt when <code>/image2video</code> runs with no override (no args lists them)' },
        { sig: '/image2video-replacement-reset', desc: 'clear all image2video replacements' },
        { sig: '/image2video-set-prompt <prompt>', desc: 'override prompt used by <code>/image2video</code> and the 🎬 button instead of each image\'s original prompt; no args shows it' },
        { sig: '/image2video-set-prompt-reset', desc: 'clear the override prompt' },
        { sig: '/image2video-workflow', desc: 'choose which image2video workflow <code>/image2video</code> uses' },
        { sig: '/image-settings', desc: 'set resolution &amp; generation steps for image generation', notes: 'resolution presets: ipad, hd, fhd, square, phone &nbsp;·&nbsp; ⇄ swaps W/H &nbsp;·&nbsp; tick <em>Use workflow default</em> to ignore the override &nbsp;·&nbsp; steps does not affect face-detail, upscale, image2image or image2video' },
        { sig: '/iterations <n>', desc: 'generate n images per prompt (default 1)' },
        { sig: '/jobs', desc: 'grid of the last 10 server-side jobs with status, cancel, and a button to pull the asset into the current chat (useful if the connection dropped mid-render)' },
        { sig: '/lora', desc: 'fuzzy-find a LoRA to insert (works anywhere in a prompt)' },
        { sig: '/multi-prompt', desc: 'generate images for multiple prompts; paste one prompt per line (Shift+Enter between lines)' },
        { sig: '/purge', desc: 'free GPU memory on the active ComfyUI server' },
        { sig: '/review <n>', desc: 'grid of the last N images, oldest first' },
        { sig: '/review-all', desc: 'grid of every image, oldest first (tap to view, trash to delete)' },
        { sig: '/review-session', desc: 'grid of this session\'s images' },
        { sig: '/review-today', desc: 'grid of today\'s images, oldest first' },
        { sig: '/sequence <master prompt>', desc: 'ask Grok to expand a master prompt into a sequence of prompts, then generate an image for each one after another', notes: 'count comes from <code>/iterations</code> (or 15 if iterations is 1) &nbsp;·&nbsp; needs <code>XAI_API_KEY</code> set on the server' },
        { sig: '/sequence-replacement <from> <to>', desc: 'find→replace applied to each Grok prompt (no args lists them)' },
        { sig: '/sequence-replacement-reset', desc: 'clear all sequence replacements' },
        { sig: '/sequence-review', desc: 'show the last sequence\'s prompts (with action/audio for a video sequence) in a grid; press ▶ on a row to generate that prompt' },
        { sig: '/server', desc: 'choose a ComfyUI server' },
        { sig: '/session-load', desc: 'pick and restore a previously saved session' },
        { sig: '/session-new', desc: 'start a completely new session, resetting all settings to defaults' },
        { sig: '/session-save <name>', desc: 'save the current session (chat history, images, settings, up/down prompt history) to disk; omit the name to pick an existing session to overwrite or delete' },
        { sig: '/session-summary', desc: 'show a summary of all active settings (server, workflow, resolution, replacements, etc.)' },
        { sig: '/slideshow <n>', desc: 'browse the last N images, oldest first' },
        { sig: '/slideshow-all', desc: 'browse every image, oldest first' },
        { sig: '/slideshow-reverse', desc: 'browse every image, newest first' },
        { sig: '/slideshow-session', desc: 'browse this session\'s images', notes: '← → keys on desktop &nbsp;·&nbsp; Del deletes the current image &nbsp;·&nbsp; swipe left/right on mobile &nbsp;·&nbsp; auto-advances every 3s' },
        { sig: '/slideshow-today', desc: 'browse today\'s images, oldest first' },
        { sig: '/splice-session', desc: 'drag this session\'s videos into order, then press ✓ to join them into one clip' },
        { sig: '/upscale [N]', desc: 'run an upscaler workflow over the last N generated images (default 1, no prompt needed)' },
        { sig: '/video-sequence <master prompt>', desc: 'like <code>/sequence</code>, but Grok also returns an action &amp; audio per shot; folded into the prompt (<code>&lt;prompt&gt;. &lt;action&gt;. Audio: &lt;audio&gt;</code>) when the image is turned into a video' },
        { sig: '/video-settings', desc: 'set video duration, frames, fps, resolution &amp; audio for image2video', notes: 'lock one value (🔒); editing either of the other two keeps <code>frames = duration × fps</code> &nbsp;·&nbsp; only one lock at a time &nbsp;·&nbsp; resolution is separate from <code>/image-settings</code> (videos have different constraints) &nbsp;·&nbsp; untick Audio to drop <code>Audio:</code> cues for workflows without sound' },
        { sig: '/workflow', desc: 'choose an image generation workflow template' },
        { sig: '/workflow-iterate <prompt>', desc: 'tick several image generation workflows, then run the prompt against each one' },
      ];

      function hlHtml(html, term) {
        if (!term) return html;
        const esc = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const re = new RegExp(esc, 'gi');
        return html.replace(/(<[^>]*>)|([^<]+)/g, (_m, tag, text) =>
          tag ? tag : text.replace(re, t => `<span style="color:#ef4444;font-weight:bold">${t}</span>`)
        );
      }

      function stripTags(html) {
        return (html || '').replace(/<[^>]*>/g, ' ').replace(/&[^;]+;/g, ' ');
      }

      const matched = helpEntries.filter(e => {
        if (!filterLc) return true;
        return (e.sig + ' ' + stripTags(e.desc) + ' ' + stripTags(e.notes || '')).toLowerCase().includes(filterLc);
      });

      const rows = matched.map(e => {
        const sigHtml  = hlHtml(escapeHtml(e.sig), filter);
        const descHtml = hlHtml(e.desc, filter);
        const inner = e.notes
          ? `<code>${sigHtml}</code> — ${descHtml}<div style="margin-top:2px;color:#475569;font-size:0.78rem">${hlHtml(e.notes, filter)}</div>`
          : `<code>${sigHtml}</code> — ${descHtml}`;
        return `<div style="font-size:0.85rem;color:#94a3b8">${inner}</div>`;
      }).join('\n          ');

      const title = filter
        ? `<strong>Commands matching "${escapeHtml(filter)}"</strong> <span style="font-size:0.8rem;color:#64748b">(${matched.length} of ${helpEntries.length})</span>`
        : '<strong>Available commands</strong>';

      const body = matched.length
        ? rows
        : `<div style="color:#64748b;font-size:0.85rem">No commands matched "${escapeHtml(filter)}"</div>`;

      addMessage('bot', `
        ${title}
        <div class="sel-list" style="margin-top:10px;gap:4px">
          ${body}
        </div>
        ${!filter ? `<div style="margin-top:10px;font-size:0.8rem;color:#475569">
          Include LoRAs in any prompt with <code>&lt;lora:name:strength&gt;</code>,
          or type <code>/lora</code> while writing a prompt to search for one
        </div>` : ''}
      `);
      return;
    }

    if (cmd === '/server') {
      const bubble = addMessage('bot', '<div class="status-text">Loading servers…</div>').parentElement.querySelector('.bubble');
      fetch('/api/servers').then(r => r.json()).then(servers => {
        const curAddr = state.currentServer ? state.currentServer.address : DEFAULT_SERVER;
        let html = '<strong>Select a server:</strong><div class="sel-list">';
        servers.forEach(s => {
          const addr = `${s.host}:${s.port}`;
          const isCur = addr === curAddr;
          html += `<button class="sel-btn${isCur ? ' current' : ''}"
                     data-addr="${addr}" data-os="${s.os || 'unix'}" data-name="${s.name}">
                     ${escapeHtml(s.name)} <span style="color:#475569;font-size:0.8em">${addr}</span>
                     ${isCur ? ' <span style="color:#7c3aed">✓</span>' : ''}
                   </button>`;
        });
        html += '</div>';
        bubble.innerHTML = html;
        bubble.querySelectorAll('.sel-btn').forEach(btn => {
          btn.addEventListener('click', () => {
            state.currentServer = { address: btn.dataset.addr, os: btn.dataset.os, name: btn.dataset.name };
            bubble.innerHTML = `Server set to <strong style="color:#a78bfa">${escapeHtml(state.currentServer.name)}</strong> <span style="color:#475569">(${state.currentServer.address})</span>`;
            deps.updateHeaderStatus();
          });
        });
        scrollBottom();
      }).catch(() => { bubble.innerHTML = '<span style="color:#f87171">Failed to load servers.</span>'; });
      return;
    }

    if (cmd === '/workflow') {
      renderWorkflowPicker({
        url: '/api/workflows',
        title: 'Select a workflow:',
        loadingText: 'Loading workflows…',
        failLabel: 'workflows',
        current: state.currentWorkflow || DEFAULT_WORKFLOW,
        setMsg: 'Workflow set to',
        onSelect: wf => { state.currentWorkflow = wf; deps.updateHeaderStatus(); },
      });
      return;
    }

    if (cmd === '/face-detail-workflow') {
      renderWorkflowPicker({
        url: '/api/facedetailer-workflows',
        title: 'Select a face-detailer workflow:',
        loadingText: 'Loading face-detailer workflows…',
        failLabel: 'face-detailer workflows',
        emptyMsg: 'No face-detailer workflows available — add one to the <code>facedetailer/</code> folder.',
        current: state.currentFaceWorkflow || DEFAULT_FACE_WORKFLOW,
        setMsg: 'Face-detailer workflow set to',
        onSelect: wf => { state.currentFaceWorkflow = wf; },
      });
      return;
    }

    if (cmd === '/addserver') {
      const name = parts[1];
      const connStr = parts[2];
      if (!name || !connStr) {
        addMessage('bot', `Usage: <code>/addserver &lt;name&gt; &lt;host:port:os&gt;</code><br>
          e.g. <code>/addserver mordor mordor:8000:windows</code>`);
        return;
      }
      const connParts = connStr.split(':');
      if (connParts.length !== 3) {
        addMessage('bot', `<span style="color:#f87171">⚠ Expected <code>host:port:os</code> — e.g. <code>mordor:8000:windows</code></span>`);
        return;
      }
      const [host, portStr, os] = connParts;
      const port = parseInt(portStr, 10);
      if (!host || isNaN(port) || !os) {
        addMessage('bot', `<span style="color:#f87171">⚠ Invalid format. Expected <code>host:port:os</code></span>`);
        return;
      }
      fetch('/api/add-server', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, host, port, os }),
      })
      .then(r => r.json())
      .then(data => {
        if (data.error) throw new Error(data.error);
        addMessage('bot', `Server <strong style="color:#a78bfa">${escapeHtml(data.name)}</strong> added (<code>${escapeHtml(data.host)}:${data.port}</code>, OS: <code>${escapeHtml(data.os)}</code>).<br>Use <code>/server</code> to select it.`);
      })
      .catch(err => addMessage('bot', `<span style="color:#f87171">⚠ ${escapeHtml(err.message)}</span>`));
      return;
    }

    if (cmd === '/slideshow-session') {
      if (!state.sessionImages.length) {
        addMessage('bot', 'No images from this session yet — generate some first!');
        return;
      }
      const bubble = addMessage('bot', '');
      state.activeSlideshowCtrl = createSlideshow(bubble, state.sessionImages.slice());
      return;
    }

    if (cmd === '/slideshow') {
      const n = parseInt(parts[1], 10);
      if (!Number.isInteger(n) || n < 1) {
        addMessage('bot', 'Usage: <code>/slideshow &lt;n&gt;</code> — browse the last N images, oldest first.');
        return;
      }
      const bubble = addMessage('bot', '<div class="status-text">Loading images…</div>');
      fetch('/api/images')
        .then(r => r.json())
        .then(images => {
          if (!images.length) {
            bubble.innerHTML = 'No images yet — generate some first!';
            return;
          }
          state.activeSlideshowCtrl = createSlideshow(bubble, images.slice(0, n).reverse());
        })
        .catch(() => { bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load images.</span>'; });
      return;
    }

    if (cmd === '/slideshow-all' || cmd === '/slideshow-today' || cmd === '/slideshow-reverse') {
      const todayOnly = cmd === '/slideshow-today';
      const reverse   = cmd === '/slideshow-reverse';
      const bubble = addMessage('bot', '<div class="status-text">Loading images…</div>');
      fetch(todayOnly ? '/api/images?filter=today' : '/api/images')
        .then(r => r.json())
        .then(images => {
          if (!images.length) {
            bubble.innerHTML = todayOnly
              ? 'No images generated today — try <code>/slideshow-all</code> for all images.'
              : 'No images yet — generate some first!';
            return;
          }
          if (!reverse) images = images.slice().reverse();
          state.activeSlideshowCtrl = createSlideshow(bubble, images);
        })
        .catch(() => { bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load images.</span>'; });
      return;
    }

    if (cmd === '/review-session') {
      if (!state.sessionImages.length) {
        addMessage('bot', 'No images from this session yet — generate some first!');
        return;
      }
      const bubble = addMessage('bot', '');
      renderReviewGrid(bubble, state.sessionImages.slice(), gridRunners);
      return;
    }

    if (cmd === '/splice-session') {
      const videos = state.sessionImages.filter(isVideoUrl);
      if (videos.length < 2) {
        addMessage('bot', videos.length
          ? 'Only one video in this session — generate at least two to splice.'
          : 'No videos from this session yet — generate some with image2video first!');
        return;
      }
      const bubble = addMessage('bot', '');
      renderCompositeGrid(bubble, videos, { compositeVideos: deps.compositeVideos });
      return;
    }

    if (cmd === '/review') {
      const n = parseInt(parts[1], 10);
      if (!Number.isInteger(n) || n < 1) {
        addMessage('bot', 'Usage: <code>/review &lt;n&gt;</code> — grid of the last N images, oldest first.');
        return;
      }
      const bubble = addMessage('bot', '<div class="status-text">Loading images…</div>');
      fetch('/api/images')
        .then(r => r.json())
        .then(images => {
          if (!images.length) {
            bubble.innerHTML = 'No images yet — generate some first!';
            return;
          }
          renderReviewGrid(bubble, images.slice(0, n).reverse(), gridRunners);
        })
        .catch(() => { bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load images.</span>'; });
      return;
    }

    if (cmd === '/review-all' || cmd === '/review-today') {
      const todayOnly = cmd === '/review-today';
      const bubble = addMessage('bot', '<div class="status-text">Loading images…</div>');
      fetch(todayOnly ? '/api/images?filter=today' : '/api/images')
        .then(r => r.json())
        .then(images => {
          if (!images.length) {
            bubble.innerHTML = todayOnly
              ? 'No images generated today — try <code>/review-all</code> for every image.'
              : 'No images yet — generate some first!';
            return;
          }
          renderReviewGrid(bubble, images.slice().reverse(), gridRunners);
        })
        .catch(() => { bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load images.</span>'; });
      return;
    }

    if (cmd === '/jobs') {
      const bubble = addMessage('bot', '<div class="status-text">Loading jobs…</div>');
      renderJobsGrid(bubble, deps);
      return;
    }

    if (cmd === '/delete') {
      if (!state.sessionImages.length) {
        addMessage('bot', 'No images from this session left to delete.');
        return;
      }
      const url = state.sessionImages[state.sessionImages.length - 1];
      const bubble = addMessage('bot', '<div class="status-text">Deleting…</div>');
      deleteImageFile(url).then(() => {
        removeImageFromChat(url);
        bubble.innerHTML = 'Deleted 1 image.';
        scrollBottom();
      }).catch(err => {
        bubble.innerHTML = `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`;
        scrollBottom();
      });
      return;
    }

    if (cmd === '/delete-session') {
      if (!state.sessionImages.length) {
        addMessage('bot', 'No images from this session left to delete.');
        return;
      }
      const targets = [...state.sessionImages];
      const bubble = addMessage('bot', '<div class="status-text">Deleting…</div>');
      Promise.allSettled(targets.map(url =>
        deleteImageFile(url).then(() => removeImageFromChat(url))
      )).then(results => {
        const failed = results.filter(r => r.status === 'rejected');
        const ok = results.length - failed.length;
        if (!failed.length) {
          state.history.length = 0;
          state.historyIdx = -1;
          state.savedDraft = '';
          state.fauxFullscreenEls.clear();
          document.body.style.overflow = '';
          messagesEl.innerHTML = '';
          addMessage('bot', 'Chat cleared. Describe the image you\'d like to generate.');
        } else {
          bubble.innerHTML = `Deleted ${ok} image(s). <span style="color:#f87171">⚠ ${failed.length} failed: ${escapeHtml(failed[0].reason.message)}</span>`;
          scrollBottom();
        }
      });
      return;
    }

    if (cmd === '/delete-today') {
      addMessage('bot', 'This deletes <strong>every</strong> image generated today, not just this session\'s. Type <code>y</code> to confirm or <code>n</code> to cancel.');
      state.pendingConfirm = (answer) => {
        if (!/^y(es)?$/i.test(answer)) {
          addMessage('bot', 'Cancelled — no images deleted.');
          return;
        }
        const bubble = addMessage('bot', '<div class="status-text">Deleting today\'s images…</div>').parentElement.querySelector('.bubble');
        fetch('/api/images?filter=today')
          .then(r => r.json())
          .then(images => {
            if (!images.length) {
              bubble.innerHTML = 'No images generated today.';
              return;
            }
            return Promise.allSettled(images.map(url =>
              deleteImageFile(url).then(() => removeImageFromChat(url))
            )).then(results => {
              const failed = results.filter(r => r.status === 'rejected');
              const ok = results.length - failed.length;
              if (failed.length) {
                bubble.innerHTML = `Deleted ${ok} image(s) generated today. <span style="color:#f87171">⚠ ${failed.length} failed: ${escapeHtml(failed[0].reason.message)}</span>`;
              } else {
                bubble.innerHTML = `Deleted ${ok} image(s) generated today.`;
              }
              scrollBottom();
            });
          })
          .catch(err => {
            bubble.innerHTML = `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`;
            scrollBottom();
          });
      };
      return;
    }

    if (cmd === '/delete-all') {
      addMessage('bot', 'This deletes <strong>every</strong> image in the output folder, not just this session\'s. Type <code>y</code> to confirm or <code>n</code> to cancel.');
      state.pendingConfirm = (answer) => {
        if (!/^y(es)?$/i.test(answer)) {
          addMessage('bot', 'Cancelled — no images deleted.');
          return;
        }
        const bubble = addMessage('bot', '<div class="status-text">Deleting all images…</div>').parentElement.querySelector('.bubble');
        fetch('/api/images', { method: 'DELETE' })
          .then(r => r.json().then(data => ({ ok: r.ok, data })))
          .then(({ ok, data }) => {
            if (!ok || data.error) throw new Error(data.error || 'Delete failed');
            state.history.length = 0;
            state.historyIdx = -1;
            state.savedDraft = '';
            state.sessionImages.length = 0;
            state.fauxFullscreenEls.clear();
            document.body.style.overflow = '';
            messagesEl.innerHTML = '';
            addMessage('bot', `Deleted ${data.deleted} image(s) from the output folder.`);
          })
          .catch(err => {
            bubble.innerHTML = `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`;
            scrollBottom();
          });
      };
      return;
    }

    if (cmd === '/archive-session') {
      if (!state.sessionImages.length) {
        addMessage('bot', 'No images from this session to archive.');
        return;
      }
      const name = raw.trim().slice(parts[0].length).trim();
      const targets = [...state.sessionImages];
      const filenames = targets.map(url => decodeURIComponent(url.split('/').pop()));
      const bubble = addMessage('bot', '<div class="status-text">Archiving…</div>').parentElement.querySelector('.bubble');
      fetch('/api/archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: 'session', filenames, name }),
      })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
          if (!ok || data.error) throw new Error(data.error || 'Archive failed');
          targets.forEach(removeImageFromChat);
          bubble.innerHTML = `Archived ${data.archived} image(s) to <code>${escapeHtml(data.folder)}</code> on the encrypted volume.`;
          scrollBottom();
        })
        .catch(err => {
          bubble.innerHTML = `<span style="color:#f87171">⚠ Archive failed: ${escapeHtml(err.message)}</span>`;
          scrollBottom();
        });
      return;
    }

    if (cmd === '/archive-today') {
      const name = raw.trim().slice(parts[0].length).trim();
      const bubble = addMessage('bot', '<div class="status-text">Archiving today\'s images…</div>').parentElement.querySelector('.bubble');
      fetch('/api/archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: 'today', name }),
      })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
          if (!ok || data.error) throw new Error(data.error || 'Archive failed');
          if (!data.archived) {
            bubble.innerHTML = 'No images generated today to archive.';
            scrollBottom();
            return;
          }
          state.history.length = 0;
          state.historyIdx = -1;
          state.savedDraft = '';
          state.sessionImages.length = 0;
          state.fauxFullscreenEls.clear();
          document.body.style.overflow = '';
          messagesEl.innerHTML = '';
          addMessage('bot', `Archived ${data.archived} image(s) generated today to <code>${escapeHtml(data.folder)}</code> on the encrypted volume.`);
        })
        .catch(err => {
          bubble.innerHTML = `<span style="color:#f87171">⚠ Archive failed: ${escapeHtml(err.message)}</span>`;
          scrollBottom();
        });
      return;
    }

    if (cmd === '/archive-all') {
      const name = raw.trim().slice(parts[0].length).trim();
      addMessage('bot', 'This archives <strong>every</strong> image in the output folder to the encrypted volume and then <strong>removes the originals</strong>. Type <code>y</code> to confirm or <code>n</code> to cancel.');
      state.pendingConfirm = (answer) => {
        if (!/^y(es)?$/i.test(answer)) {
          addMessage('bot', 'Cancelled — nothing archived.');
          return;
        }
        const bubble = addMessage('bot', '<div class="status-text">Archiving all images…</div>').parentElement.querySelector('.bubble');
        fetch('/api/archive', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope: 'all', name }),
        })
          .then(r => r.json().then(data => ({ ok: r.ok, data })))
          .then(({ ok, data }) => {
            if (!ok || data.error) throw new Error(data.error || 'Archive failed');
            state.history.length = 0;
            state.historyIdx = -1;
            state.savedDraft = '';
            state.sessionImages.length = 0;
            state.fauxFullscreenEls.clear();
            document.body.style.overflow = '';
            messagesEl.innerHTML = '';
            addMessage('bot', `Archived ${data.archived} image(s) from the output folder to <code>${escapeHtml(data.folder)}</code> on the encrypted volume.`);
          })
          .catch(err => {
            bubble.innerHTML = `<span style="color:#f87171">⚠ Archive failed: ${escapeHtml(err.message)}</span>`;
            scrollBottom();
          });
      };
      return;
    }

    if (cmd === '/clear') {
      state.fauxFullscreenEls.clear();
      document.body.style.overflow = '';
      messagesEl.innerHTML = '';
      addMessage('bot', 'Chat cleared. Settings, prompt history and session images preserved — describe the image you\'d like to generate.');
      return;
    }

    if (cmd === '/session-new') {
      state.history.length = 0;
      state.historyIdx = -1;
      state.savedDraft = '';
      state.sessionImages.length = 0;
      for (const k of Object.keys(state.imagePrompts)) delete state.imagePrompts[k];
      for (const k of Object.keys(state.imageVideoMeta)) delete state.imageVideoMeta[k];
      state.lastSequence = null;
      state.fauxFullscreenEls.clear();
      document.body.style.overflow = '';
      state.currentServer = null;
      state.currentWorkflow = null;
      state.currentFaceWorkflow = null;
      state.currentUpscaleWorkflow = null;
      state.currentImage2ImageWorkflow = null;
      state.currentImage2VideoWorkflow = null;
      state.currentInpaintingWorkflow = null;
      state.lastFaceDetailPrompt = null;
      state.lastInpaintingPrompt = null;
      state.currentResolution = { width: 1365, height: 768 };
      state.currentGenerationSteps = null;
      state.currentDenoise = { ...DEFAULT_DENOISE };
      state.currentVideoSettings = { ...DEFAULT_VIDEO_SETTINGS };
      state.videoLock = 'fps';
      state.iterations = 1;
      state.iterationsFromSequence = false;
      state.sequenceReplacements = [];
      state.image2imageReplacements = [];
      state.image2imageOverridePrompt = null;
      state.image2videoReplacements = [];
      state.image2videoOverridePrompt = null;
      messagesEl.innerHTML = '';
      deps.updateHeaderStatus();
      addMessage('bot', 'New session started. Describe the image you\'d like to generate.');
      return;
    }

    if (cmd === '/session-save') {
      const rawName = raw.slice('/session-save'.length).trim();
      addMessage('user', escapeHtml(raw), raw);

      const buildPayload = (name) => ({
        name,
        settings: {
          server: state.currentServer,
          workflow: state.currentWorkflow,
          faceWorkflow: state.currentFaceWorkflow,
          upscaleWorkflow: state.currentUpscaleWorkflow,
          image2imageWorkflow: state.currentImage2ImageWorkflow,
          image2videoWorkflow: state.currentImage2VideoWorkflow,
          inpaintingWorkflow: state.currentInpaintingWorkflow,
          resolution: state.currentResolution,
          generationSteps: state.currentGenerationSteps,
          iterations: state.iterations,
          sequenceReplacements: state.sequenceReplacements.slice(),
          image2imageReplacements: state.image2imageReplacements.slice(),
          image2imageOverridePrompt: state.image2imageOverridePrompt,
          image2videoReplacements: state.image2videoReplacements.slice(),
          image2videoOverridePrompt: state.image2videoOverridePrompt,
          lastFaceDetailPrompt: state.lastFaceDetailPrompt,
          lastInpaintingPrompt: state.lastInpaintingPrompt,
          currentDenoise: { ...state.currentDenoise },
          videoSettings: { ...state.currentVideoSettings },
          videoLock: state.videoLock,
        },
        sessionImages: state.sessionImages.slice(),
        imagePrompts: Object.assign({}, state.imagePrompts),
        imageVideoMeta: Object.assign({}, state.imageVideoMeta),
        lastSequence: state.lastSequence,
        promptHistory: state.history.slice(),
        messages: deps.captureSessionMessages(),
      });

      const doSave = (name, bubble) => {
        bubble.innerHTML = '<div class="status-text">Saving session…</div>';
        fetch('/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(buildPayload(name)),
        })
        .then(parseJsonResponse)
        .then(data => {
          if (data.error) throw new Error(data.error);
          bubble.innerHTML = `Session saved as <strong style="color:#a78bfa">${escapeHtml(data.name)}</strong>. Use <code>/session-load</code> to restore it.`;
          scrollBottom();
        })
        .catch(err => {
          bubble.innerHTML = `<span style="color:#f87171">⚠ Save failed: ${escapeHtml(err.message)}</span>`;
          scrollBottom();
        });
      };

      if (rawName) {
        const bubble = addMessage('bot', '').parentElement.querySelector('.bubble');
        doSave(rawName, bubble);
        return;
      }

      renderSessionPicker({
        headerHtml: '<strong>Select a session to overwrite:</strong>',
        onSelect: (name, bubble) => doSave(name, bubble),
      });
      return;
    }

    if (cmd === '/session-load') {
      renderSessionPicker({
        headerHtml: '<strong>Select a session to restore:</strong>',
        onSelect: (name, bubble) => {
          bubble.innerHTML = `<div class="status-text">Loading <strong>${escapeHtml(name)}</strong>…</div>`;
          fetch('/api/sessions/' + encodeURIComponent(name))
          .then(parseJsonResponse)
          .then(data => {
            if (data.error) throw new Error(data.error);
            deps.restoreSession(data);
            bubble.innerHTML = `Session <strong style="color:#a78bfa">${escapeHtml(name)}</strong> restored.`;
            scrollBottom();
            showSessionSummary();
          })
          .catch(err => {
            bubble.innerHTML = `<span style="color:#f87171">⚠ Load failed: ${escapeHtml(err.message)}</span>`;
            scrollBottom();
          });
        },
      });
      return;
    }

    if (cmd === '/session-summary') {
      showSessionSummary();
      return;
    }

    if (cmd === '/purge') {
      const bubble = addMessage('bot', '<div class="status-text">Freeing GPU memory…</div><div class="dots"><span></span><span></span><span></span></div>').parentElement.querySelector('.bubble');
      const server = state.currentServer ? state.currentServer.address : null;
      fetch('/api/purge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(server ? { server } : {}),
      })
      .then(r => r.json())
      .then(data => {
        if (data.error) throw new Error(data.error);
        bubble.innerHTML = 'GPU memory freed.';
        scrollBottom();
      })
      .catch(err => {
        bubble.innerHTML = `<span style="color:#f87171">⚠ Purge failed: ${escapeHtml(err.message)}</span>`;
        scrollBottom();
      });
      return;
    }

    if (cmd === '/lora') {
      addMessage('bot', 'Type <code>/lora</code> followed by a search term while writing a prompt, then pick a LoRA from the list — it inserts a <code>&lt;lora:name:strength&gt;</code> tag at the cursor.');
      return;
    }

    if (cmd === '/image-settings') {
      addMessage('user', escapeHtml(raw), raw);
      const DEFAULT_RES = { width: 1365, height: 768 };
      const work = {
        width:  state.currentResolution ? state.currentResolution.width  : DEFAULT_RES.width,
        height: state.currentResolution ? state.currentResolution.height : DEFAULT_RES.height,
        useDefaultRes: !state.currentResolution,
        steps: state.currentGenerationSteps !== null ? state.currentGenerationSteps : 20,
        useDefaultSteps: state.currentGenerationSteps === null,
      };

      const wrap = document.createElement('div');
      wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-top:6px';

      const resRow = document.createElement('div');
      resRow.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.85rem;color:#cbd5e1;flex-wrap:wrap';
      const resLbl = document.createElement('span');
      resLbl.textContent = 'Resolution:';
      resLbl.style.cssText = 'min-width:92px;color:#94a3b8';
      const mkDim = key => {
        const inp = document.createElement('input');
        inp.type = 'text';
        inp.style.cssText = 'width:62px;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#f1f5f9;padding:2px 4px;font-size:0.85rem;text-align:center';
        inp.addEventListener('change', () => {
          const v = parseInt(inp.value, 10);
          if (!isNaN(v)) work[key] = Math.min(8192, Math.max(64, v));
          inp.value = String(work[key]);
          work.useDefaultRes = false;
          defaultResBox.checked = false;
        });
        return inp;
      };
      const widthInp  = mkDim('width');
      const heightInp = mkDim('height');
      const times = document.createElement('span');
      times.textContent = '×';
      times.style.color = '#475569';
      const flipBtn = document.createElement('button');
      flipBtn.textContent = '⇄';
      flipBtn.className = 'sel-btn';
      flipBtn.title = 'Swap width and height';
      flipBtn.style.cssText = 'flex:none;width:30px;padding:2px 0;font-size:0.95rem;line-height:1';
      const refreshRes = () => { widthInp.value = String(work.width); heightInp.value = String(work.height); };
      flipBtn.addEventListener('click', () => {
        [work.width, work.height] = [work.height, work.width];
        work.useDefaultRes = false;
        defaultResBox.checked = false;
        refreshRes();
      });
      resRow.appendChild(resLbl); resRow.appendChild(widthInp); resRow.appendChild(times);
      resRow.appendChild(heightInp); resRow.appendChild(flipBtn);
      wrap.appendChild(resRow);

      const presetRow = document.createElement('div');
      presetRow.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:0.8rem;flex-wrap:wrap;padding-left:100px';
      const setRes = (w, h) => {
        work.width = w; work.height = h;
        work.useDefaultRes = false;
        defaultResBox.checked = false;
        refreshRes();
      };
      const mkPreset = (label, onClick) => {
        const b = document.createElement('button');
        b.textContent = label;
        b.className = 'sel-btn';
        b.style.cssText = 'flex:none;padding:2px 8px;font-size:0.78rem;color:#94a3b8';
        b.addEventListener('click', onClick);
        return b;
      };
      Object.entries(RESOLUTION_PRESETS).forEach(([key, p]) => {
        presetRow.appendChild(mkPreset(key, () => setRes(p.width, p.height)));
      });
      presetRow.appendChild(mkPreset('phone', () => {
        const dpr  = window.devicePixelRatio || 1;
        const snap = v => Math.round(v / 8) * 8;
        const physW = snap(window.screen.width  * dpr);
        const physH = snap(window.screen.height * dpr);
        setRes(Math.min(physW, physH), Math.max(physW, physH));
      }));
      wrap.appendChild(presetRow);

      const defaultResRow = document.createElement('label');
      defaultResRow.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.82rem;color:#cbd5e1;cursor:pointer;padding-left:100px';
      const defaultResBox = document.createElement('input');
      defaultResBox.type = 'checkbox';
      defaultResBox.checked = work.useDefaultRes;
      defaultResBox.style.cssText = 'width:14px;height:14px;accent-color:#f472b6;cursor:pointer';
      defaultResBox.addEventListener('change', () => { work.useDefaultRes = defaultResBox.checked; });
      const defaultResLbl = document.createElement('span');
      defaultResLbl.innerHTML = 'Use workflow default <span style="color:#475569">— ignore the resolution above</span>';
      defaultResRow.appendChild(defaultResBox); defaultResRow.appendChild(defaultResLbl);
      wrap.appendChild(defaultResRow);

      const stepsRow = document.createElement('div');
      stepsRow.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.85rem;color:#cbd5e1;margin-top:4px';
      const stepsLbl = document.createElement('span');
      stepsLbl.textContent = 'Steps:';
      stepsLbl.style.cssText = 'min-width:92px;color:#94a3b8';
      const stepsSlider = document.createElement('input');
      stepsSlider.type = 'range';
      stepsSlider.min = '1'; stepsSlider.max = '100'; stepsSlider.step = '1';
      stepsSlider.value = String(work.steps);
      stepsSlider.style.cssText = 'width:130px;accent-color:#f472b6;cursor:pointer';
      const stepsInp = document.createElement('input');
      stepsInp.type = 'text';
      stepsInp.value = String(work.steps);
      stepsInp.style.cssText = 'width:52px;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#f1f5f9;padding:2px 4px;font-size:0.85rem;text-align:center';
      const onStepsEdit = v => {
        const n = parseInt(v, 10);
        if (isNaN(n)) { stepsInp.value = String(work.steps); return; }
        work.steps = Math.min(200, Math.max(1, n));
        stepsInp.value = String(work.steps);
        stepsSlider.value = String(Math.min(100, work.steps));
        work.useDefaultSteps = false;
        defaultStepsBox.checked = false;
      };
      stepsSlider.addEventListener('input', () => onStepsEdit(stepsSlider.value));
      stepsInp.addEventListener('change', () => onStepsEdit(stepsInp.value));
      stepsRow.appendChild(stepsLbl); stepsRow.appendChild(stepsSlider); stepsRow.appendChild(stepsInp);
      wrap.appendChild(stepsRow);

      const defaultStepsRow = document.createElement('label');
      defaultStepsRow.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.82rem;color:#cbd5e1;cursor:pointer;padding-left:100px';
      const defaultStepsBox = document.createElement('input');
      defaultStepsBox.type = 'checkbox';
      defaultStepsBox.checked = work.useDefaultSteps;
      defaultStepsBox.style.cssText = 'width:14px;height:14px;accent-color:#f472b6;cursor:pointer';
      defaultStepsBox.addEventListener('change', () => { work.useDefaultSteps = defaultStepsBox.checked; });
      const defaultStepsLbl = document.createElement('span');
      defaultStepsLbl.innerHTML = 'Use workflow default <span style="color:#475569">— does not affect face-detail, upscale, image2image or image2video</span>';
      defaultStepsRow.appendChild(defaultStepsBox); defaultStepsRow.appendChild(defaultStepsLbl);
      wrap.appendChild(defaultStepsRow);

      const btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;gap:8px;margin-top:4px';
      const applyBtn = document.createElement('button');
      applyBtn.textContent = 'Apply';
      applyBtn.className = 'sel-btn';
      applyBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem';
      const resetBtn = document.createElement('button');
      resetBtn.textContent = 'Reset to defaults';
      resetBtn.className = 'sel-btn';
      resetBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem;color:#94a3b8';
      applyBtn.addEventListener('click', () => {
        state.currentResolution = work.useDefaultRes ? null : { width: work.width, height: work.height };
        state.currentGenerationSteps = work.useDefaultSteps ? null : work.steps;
        const resTxt = state.currentResolution
          ? `<strong style="color:#a78bfa">${state.currentResolution.width}×${state.currentResolution.height}</strong>`
          : '<span style="color:#475569">workflow default</span>';
        const stepsTxt = state.currentGenerationSteps !== null
          ? `<strong style="color:#a78bfa">${state.currentGenerationSteps}</strong>`
          : '<span style="color:#475569">workflow default</span>';
        addMessage('bot', `Image settings set — Resolution ${resTxt} · Steps ${stepsTxt}`);
        scrollBottom();
      });
      resetBtn.addEventListener('click', () => {
        work.width = DEFAULT_RES.width; work.height = DEFAULT_RES.height;
        work.useDefaultRes = false;
        work.steps = 20; work.useDefaultSteps = true;
        defaultResBox.checked = false;
        defaultStepsBox.checked = true;
        stepsSlider.value = String(work.steps);
        stepsInp.value = String(work.steps);
        refreshRes();
      });
      btnRow.appendChild(applyBtn);
      btnRow.appendChild(resetBtn);
      wrap.appendChild(btnRow);

      refreshRes();
      const bubble = addMessage('bot', '<strong>Image settings</strong> <span style="color:#475569">(image generation)</span>').parentElement.querySelector('.bubble');
      bubble.appendChild(wrap);
      scrollBottom();
      return;
    }

    if (cmd === '/iterations') {
      if (!parts[1]) {
        addMessage('bot', `Each prompt currently generates <strong style="color:#a78bfa">${state.iterations}</strong> image(s).<br>Usage: <code>/iterations &lt;n&gt;</code> — e.g. <code>/iterations 8</code>`);
        return;
      }
      const n = parseInt(parts[1], 10);
      if (isNaN(n) || n < 1 || n > 64) {
        addMessage('bot', '<span style="color:#f87171">⚠ Iterations must be a number between 1 and 64.</span>');
        return;
      }
      state.iterations = n;
      state.iterationsFromSequence = false;
      addMessage('bot', `Each prompt will now generate <strong style="color:#a78bfa">${state.iterations}</strong> image(s)${n > 1 ? ', one after another' : ''}.`);
      return;
    }

    if (cmd === '/denoise') {
      addMessage('user', escapeHtml(raw), raw);
      const DENOISE_ROWS = [
        { key: 'face',        label: 'Face-detailer' },
        { key: 'image2image', label: 'Image2image'   },
        { key: 'inpaint',     label: 'Inpainting'    },
        { key: 'upscale',     label: 'Upscale'       },
      ];
      const wrap = document.createElement('div');
      wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-top:6px';
      const sliders = {};
      const inputs  = {};
      DENOISE_ROWS.forEach(({ key, label }) => {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.85rem;color:#cbd5e1';
        const lbl = document.createElement('span');
        lbl.textContent = label + ':';
        lbl.style.cssText = 'min-width:110px;color:#94a3b8';
        const sl = document.createElement('input');
        sl.type = 'range'; sl.min = '0.01'; sl.max = '1'; sl.step = '0.01';
        sl.value = state.currentDenoise[key].toFixed(2);
        sl.style.cssText = 'width:130px;accent-color:#f472b6;cursor:pointer';
        const inp = document.createElement('input');
        inp.type = 'text'; inp.value = state.currentDenoise[key].toFixed(2);
        inp.style.cssText = 'width:44px;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#f1f5f9;padding:2px 4px;font-size:0.85rem;text-align:center';
        sl.addEventListener('input', () => { inp.value = parseFloat(sl.value).toFixed(2); });
        inp.addEventListener('change', () => {
          let v = parseFloat(inp.value);
          if (isNaN(v)) v = state.currentDenoise[key];
          v = Math.min(1, Math.max(0.01, Math.round(v * 100) / 100));
          inp.value = v.toFixed(2);
          sl.value  = v.toFixed(2);
        });
        sliders[key] = sl; inputs[key] = inp;
        row.appendChild(lbl); row.appendChild(sl); row.appendChild(inp);
        wrap.appendChild(row);
      });
      const btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;gap:8px;margin-top:4px';
      const applyBtn = document.createElement('button');
      applyBtn.textContent = 'Apply';
      applyBtn.className = 'sel-btn';
      applyBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem';
      const resetBtn = document.createElement('button');
      resetBtn.textContent = 'Reset to defaults';
      resetBtn.className = 'sel-btn';
      resetBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem;color:#94a3b8';
      applyBtn.addEventListener('click', () => {
        DENOISE_ROWS.forEach(({ key }) => {
          state.currentDenoise[key] = parseFloat(sliders[key].value);
        });
        const summary = DENOISE_ROWS.map(({ key, label }) =>
          `${label}: <strong style="color:#a78bfa">${state.currentDenoise[key].toFixed(2)}</strong>`).join(' · ');
        addMessage('bot', `Denoise defaults set — ${summary}`);
        scrollBottom();
      });
      resetBtn.addEventListener('click', () => {
        state.currentDenoise = { ...DEFAULT_DENOISE };
        DENOISE_ROWS.forEach(({ key }) => {
          sliders[key].value = DEFAULT_DENOISE[key].toFixed(2);
          inputs[key].value  = DEFAULT_DENOISE[key].toFixed(2);
        });
        addMessage('bot', 'Denoise defaults reset to: Face-detailer <strong style="color:#a78bfa">0.35</strong> · Image2image <strong style="color:#a78bfa">0.30</strong> · Inpainting <strong style="color:#a78bfa">0.45</strong> · Upscale <strong style="color:#a78bfa">0.15</strong>');
        scrollBottom();
      });
      btnRow.appendChild(applyBtn);
      btnRow.appendChild(resetBtn);
      wrap.appendChild(btnRow);
      const bubble = addMessage('bot', '<strong>Denoise defaults</strong>').parentElement.querySelector('.bubble');
      bubble.appendChild(wrap);
      scrollBottom();
      return;
    }

    if (cmd === '/video-settings') {
      addMessage('user', escapeHtml(raw), raw);
      const VIDEO_ROWS = [
        { key: 'duration', label: 'Duration (s)' },
        { key: 'frames',   label: 'Frames'       },
        { key: 'fps',      label: 'FPS'          },
      ];
      const work    = { ...state.currentVideoSettings };
      let   lockSel = state.videoLock;
      const els = {};

      const wrap = document.createElement('div');
      wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-top:6px';

      function refresh() {
        VIDEO_ROWS.forEach(({ key }) => {
          const locked = lockSel === key;
          const { lockBtn, slider, input } = els[key];
          lockBtn.textContent = locked ? '🔒' : '🔓';
          lockBtn.title = locked ? `${key} is locked — drag the others` : `lock ${key}`;
          slider.disabled = locked;
          input.disabled  = locked;
          slider.style.opacity = locked ? '0.35' : '';
          slider.value = String(work[key]);
          input.value  = key === 'duration' ? fmtDuration(work[key]) : String(work[key]);
        });
      }

      function onEdit(key, rawVal) {
        const v = parseFloat(rawVal);
        if (isNaN(v)) { refresh(); return; }
        work[key] = clampVideo(key, v);
        recomputeVideo(work, lockSel, key);
        refresh();
      }

      VIDEO_ROWS.forEach(({ key, label }) => {
        const lim = VIDEO_LIMITS[key];
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.85rem;color:#cbd5e1';

        const lockBtn = document.createElement('button');
        lockBtn.className = 'sel-btn';
        lockBtn.style.cssText = 'flex:none;width:30px;padding:2px 0;font-size:0.95rem;line-height:1';
        lockBtn.addEventListener('click', () => {
          if (lockSel === key) return;
          lockSel = key;
          refresh();
        });

        const lbl = document.createElement('span');
        lbl.textContent = label + ':';
        lbl.style.cssText = 'min-width:92px;color:#94a3b8';

        const slider = document.createElement('input');
        slider.type = 'range';
        slider.min = String(lim.min); slider.max = String(lim.max);
        slider.step = key === 'duration' ? '0.1' : '1';
        slider.style.cssText = 'width:130px;accent-color:#f472b6;cursor:pointer';
        slider.addEventListener('input', () => onEdit(key, slider.value));

        const input = document.createElement('input');
        input.type = 'text';
        input.style.cssText = 'width:52px;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#f1f5f9;padding:2px 4px;font-size:0.85rem;text-align:center';
        input.addEventListener('change', () => onEdit(key, input.value));

        els[key] = { lockBtn, slider, input };
        row.appendChild(lockBtn); row.appendChild(lbl); row.appendChild(slider); row.appendChild(input);
        wrap.appendChild(row);
      });

      const resRow = document.createElement('div');
      resRow.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.85rem;color:#cbd5e1';
      const resLbl = document.createElement('span');
      resLbl.textContent = 'Resolution:';
      resLbl.style.cssText = 'min-width:92px;color:#94a3b8';
      const mkDim = key => {
        const inp = document.createElement('input');
        inp.type = 'text';
        inp.style.cssText = 'width:58px;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#f1f5f9;padding:2px 4px;font-size:0.85rem;text-align:center';
        inp.addEventListener('change', () => {
          const v = parseFloat(inp.value);
          if (!isNaN(v)) work[key] = clampVideo(key, v);
          inp.value = String(work[key]);
        });
        return inp;
      };
      const widthInp  = mkDim('width');
      const heightInp = mkDim('height');
      const times = document.createElement('span');
      times.textContent = '×';
      times.style.color = '#475569';
      const flipBtn = document.createElement('button');
      flipBtn.textContent = '⇄';
      flipBtn.className = 'sel-btn';
      flipBtn.title = 'Swap width and height';
      flipBtn.style.cssText = 'flex:none;width:30px;padding:2px 0;font-size:0.95rem;line-height:1';
      const refreshRes = () => { widthInp.value = String(work.width); heightInp.value = String(work.height); };
      flipBtn.addEventListener('click', () => { [work.width, work.height] = [work.height, work.width]; refreshRes(); });
      resRow.appendChild(resLbl); resRow.appendChild(widthInp); resRow.appendChild(times);
      resRow.appendChild(heightInp); resRow.appendChild(flipBtn);
      wrap.appendChild(resRow);
      refreshRes();

      const audioRow = document.createElement('label');
      audioRow.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.85rem;color:#cbd5e1;cursor:pointer';
      const audioBox = document.createElement('input');
      audioBox.type = 'checkbox';
      audioBox.checked = work.audio !== false;
      audioBox.style.cssText = 'width:15px;height:15px;accent-color:#f472b6;cursor:pointer';
      audioBox.addEventListener('change', () => { work.audio = audioBox.checked; });
      const audioLbl = document.createElement('span');
      audioLbl.innerHTML = 'Audio <span style="color:#475569">— include <code>Audio:</code> cues in video prompts</span>';
      audioRow.appendChild(audioBox); audioRow.appendChild(audioLbl);
      wrap.appendChild(audioRow);

      const hint = document.createElement('div');
      hint.style.cssText = 'font-size:0.78rem;color:#475569;margin-top:2px';
      hint.innerHTML = 'Lock one value (🔒), then drag or type the others — the third follows so that <code>frames = duration × fps</code>.';
      wrap.appendChild(hint);

      const btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;gap:8px;margin-top:4px';
      const applyBtn = document.createElement('button');
      applyBtn.textContent = 'Apply';
      applyBtn.className = 'sel-btn';
      applyBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem';
      const resetBtn = document.createElement('button');
      resetBtn.textContent = 'Reset to defaults';
      resetBtn.className = 'sel-btn';
      resetBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem;color:#94a3b8';
      applyBtn.addEventListener('click', () => {
        state.currentVideoSettings = { ...work };
        state.videoLock = lockSel;
        addMessage('bot', `Video settings set — Duration <strong style="color:#a78bfa">${fmtDuration(work.duration)}s</strong> · Frames <strong style="color:#a78bfa">${work.frames}</strong> · FPS <strong style="color:#a78bfa">${work.fps}</strong> · Resolution <strong style="color:#a78bfa">${work.width}×${work.height}</strong> · Audio <strong style="color:#a78bfa">${work.audio !== false ? 'on' : 'off'}</strong> <span style="color:#475569">(🔒 ${lockSel})</span>`);
        scrollBottom();
      });
      resetBtn.addEventListener('click', () => {
        Object.assign(work, DEFAULT_VIDEO_SETTINGS);
        lockSel = 'fps';
        audioBox.checked = work.audio !== false;
        refreshRes();
        refresh();
      });
      btnRow.appendChild(applyBtn);
      btnRow.appendChild(resetBtn);
      wrap.appendChild(btnRow);

      refresh();
      const bubble = addMessage('bot', '<strong>Video settings</strong> <span style="color:#475569">(image2video)</span>').parentElement.querySelector('.bubble');
      bubble.appendChild(wrap);
      scrollBottom();
      return;
    }

    if (cmd === '/alias-create') {
      const argStr = raw.slice('/alias-create'.length).trim();
      addMessage('user', escapeHtml(raw), raw);
      if (!argStr) {
        addMessage('bot', 'Usage: <code>/alias-create &lt;word&gt; &lt;expansion&gt;</code><br>' +
          'e.g. <code>/alias-create prophoto "Professional Photo, Medium format look"</code><br>' +
          'Quotes around the expansion are optional but useful when it contains special characters.');
        return;
      }
      const spaceIdx = argStr.indexOf(' ');
      if (spaceIdx === -1) {
        addMessage('bot', '<span style="color:#f87171">⚠ Provide both a word and an expansion — e.g. <code>/alias-create prophoto "Professional Photo, Medium format look"</code></span>');
        return;
      }
      const aliasFrom = argStr.slice(0, spaceIdx);
      let aliasTo = argStr.slice(spaceIdx + 1).trim();
      if ((aliasTo.startsWith('"') && aliasTo.endsWith('"')) ||
          (aliasTo.startsWith("'") && aliasTo.endsWith("'"))) {
        aliasTo = aliasTo.slice(1, -1).trim();
      } else if (aliasTo.startsWith('"') || aliasTo.startsWith("'")) {
        aliasTo = aliasTo.slice(1).trim();
      }
      if (!aliasTo) {
        addMessage('bot', '<span style="color:#f87171">⚠ Expansion cannot be empty.</span>');
        return;
      }
      const bubble = addMessage('bot', '<div class="status-text">Saving alias…</div>').parentElement.querySelector('.bubble');
      fetch('/api/aliases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from: aliasFrom, to: aliasTo }),
      })
      .then(parseJsonResponse)
      .then(data => {
        if (data.error) throw new Error(data.error);
        state.ALIASES[aliasFrom] = aliasTo;
        const verb = data.updated ? 'Updated' : 'Created';
        bubble.innerHTML = `${verb} alias: <code>${escapeHtml(aliasFrom)}</code> → <code>${escapeHtml(aliasTo)}</code>`;
        scrollBottom();
      })
      .catch(err => {
        bubble.innerHTML = `<span style="color:#f87171">⚠ ${escapeHtml(err.message)}</span>`;
        scrollBottom();
      });
      return;
    }

    if (cmd === '/alias-list') {
      addMessage('user', escapeHtml(raw), raw);
      const entries = Object.entries(state.ALIASES).sort(([a], [b]) => a.localeCompare(b));
      if (!entries.length) {
        addMessage('bot', 'No aliases defined. Use <code>/alias-create &lt;word&gt; &lt;expansion&gt;</code> to create one.');
        return;
      }
      const bubble = addMessage('bot', '').parentElement.querySelector('.bubble');
      const header = document.createElement('strong');
      header.textContent = `Aliases (${entries.length}):`;
      const selList = document.createElement('div');
      selList.className = 'sel-list';
      selList.style.cssText = 'margin-top:8px;gap:4px';

      entries.forEach(([k, v]) => {
        const row = document.createElement('div');
        row.className = 'sel-row';

        const label = document.createElement('div');
        label.style.cssText = 'font-size:0.85rem;color:#94a3b8;flex:1;min-width:0;overflow-wrap:break-word';
        label.innerHTML = `<code>${escapeHtml(k)}</code> → <code>${escapeHtml(v)}</code>`;

        const delBtn = document.createElement('button');
        delBtn.className = 'sel-del-btn';
        delBtn.title = 'Delete alias';
        delBtn.innerHTML = '&#128465;&#xFE0E;';
        delBtn.addEventListener('click', e => {
          e.stopPropagation();
          delBtn.disabled = true;
          delBtn.style.opacity = '0.4';
          fetch('/api/aliases/' + encodeURIComponent(k), { method: 'DELETE' })
          .then(parseJsonResponse)
          .then(data => {
            if (data.error) throw new Error(data.error);
            delete state.ALIASES[k];
            row.remove();
            if (!selList.querySelector('.sel-row')) {
              bubble.innerHTML = 'No aliases defined. Use <code>/alias-create &lt;word&gt; &lt;expansion&gt;</code> to create one.';
            } else {
              header.textContent = `Aliases (${selList.querySelectorAll('.sel-row').length}):`;
            }
            scrollBottom();
          })
          .catch(err => {
            delBtn.disabled = false;
            delBtn.style.opacity = '';
            addMessage('bot', `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`);
            scrollBottom();
          });
        });

        row.appendChild(label);
        row.appendChild(delBtn);
        selList.appendChild(row);
      });

      bubble.appendChild(header);
      bubble.appendChild(selList);
      scrollBottom();
      return;
    }

    addMessage('bot', `Unknown command <code>${escapeHtml(cmd)}</code> — type <code>/help</code> for available commands.`);
  }

  return { handleSlashCommand };
}
