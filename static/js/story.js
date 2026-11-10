// /video-story — the review/edit table for a continuous story of video clips.
//
// state.videoStory = { master, clipSeconds, items: [{description, soundscape, music}],
// cursor }. Each row's 📋 copies that row into state.lastVideoMeta — exactly what the
// metadata editor's Clone button reads — and moves the story cursor to it, so the ⏭
// button on a video (chat.js) carries on from the following row.
//
// Every change dispatches 'video-story-changed'; chat.js listens to it to save the
// session and refresh the ⏭ buttons, so this module needs nothing from chat.js.

import { escapeHtml, videoStoryInsert, videoStoryDelete, videoStorySetCursor } from './utils.js';
import { state } from './state.js';
import { addMessage, scrollBottom } from './dom.js';

export function notifyVideoStoryChanged() {
  document.dispatchEvent(new CustomEvent('video-story-changed'));
}

function fmtSeconds(s) {
  const n = Number(s) || 0;
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function renderVideoStoryTable(bubble) {
  // Re-rendering a bubble must not stack 'video-story-changed' listeners.
  if (bubble._storyCleanup) bubble._storyCleanup();
  bubble._storyCleanup = null;
  const story = state.videoStory;
  bubble.innerHTML = '';
  if (!story) return;

  // Every action re-reads state: a newer /video-story may have replaced this one.
  const isLive = () => state.videoStory === story;

  const header = document.createElement('div');
  const summary = document.createElement('div');
  header.appendChild(summary);
  const master = document.createElement('div');
  master.style.cssText = 'color:#94a3b8;font-size:0.85rem;margin-top:2px';
  master.textContent = story.master || '';
  header.appendChild(master);
  const help = document.createElement('div');
  help.style.cssText = 'color:#64748b;font-size:0.8rem;margin-top:4px';
  help.textContent = 'Edit any cell. Click a row to make it the current shot (click it again to clear). ' +
    '📋 copies a row for the metadata editor\'s Clone button and makes it current; ⏭ on a video cuts its ' +
    'last frame and starts the shot after the current one. Insert adds a blank shot after the current one; ' +
    'Delete removes the current shot.';
  header.appendChild(help);
  bubble.appendChild(header);

  const table = document.createElement('table');
  table.className = 'story-table';
  table.innerHTML = '<thead><tr><th>#</th><th>Description</th><th>Soundscape</th><th></th></tr></thead>';
  const tbody = document.createElement('tbody');
  table.appendChild(tbody);
  bubble.appendChild(table);

  const actions = document.createElement('div');
  actions.className = 'story-actions';
  const insertBtn = document.createElement('button');
  insertBtn.className = 'sel-btn';
  insertBtn.textContent = '➕ Insert row';
  insertBtn.title = 'Insert a blank shot after the current one (at the top when none is current)';
  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'sel-btn';
  deleteBtn.textContent = '🗑 Delete row';
  deleteBtn.title = 'Delete the current shot';
  actions.append(insertBtn, deleteBtn);
  bubble.appendChild(actions);

  let rows = [];
  const markCurrent = () => {
    rows.forEach((tr, i) => tr.classList.toggle('story-current', i === story.cursor));
  };
  const syncButtons = () => {
    const live = isLive();
    insertBtn.disabled = !live;
    deleteBtn.disabled = !live || !(story.cursor >= 0 && story.cursor < story.items.length);
  };

  const renderRows = () => {
    summary.innerHTML = `<strong>Video story</strong> — ${story.items.length} clip(s) of about ${escapeHtml(fmtSeconds(story.clipSeconds))}s`;
    tbody.innerHTML = '';
    rows = [];
    story.items.forEach((item, i) => {
      const tr = document.createElement('tr');
      tr.title = 'Click to make this the current shot';
      tr.addEventListener('click', e => {
        if (e.target.closest('textarea, button') || !isLive()) return;
        videoStorySetCursor(story, i);
        notifyVideoStoryChanged();
      });

      const num = document.createElement('td');
      num.className = 'story-num';
      num.textContent = String(i + 1);
      tr.appendChild(num);

      for (const key of ['description', 'soundscape']) {
        const td = document.createElement('td');
        const ta = document.createElement('textarea');
        ta.value = item[key] || '';
        ta.addEventListener('input', () => {
          item[key] = ta.value;
          notifyVideoStoryChanged();
        });
        td.appendChild(ta);
        tr.appendChild(td);
      }

      const tdBtn = document.createElement('td');
      const copy = document.createElement('button');
      copy.className = 'sel-btn';
      copy.textContent = '📋';
      copy.title = 'Copy this shot for the metadata editor\'s Clone button and make it the current shot';
      copy.style.cssText = 'flex:none;padding:2px 8px;font-size:0.85rem;line-height:1.2';
      copy.addEventListener('click', () => {
        // Raw fields, as runImage2Video records them — Clone fills the editor from these.
        state.lastVideoMeta = {
          prompt: '',
          description: (item.description || '').trim(),
          soundscape: (item.soundscape || '').trim(),
          music: (item.music || 'N/A').trim(),
        };
        if (isLive()) {
          story.cursor = i;
          notifyVideoStoryChanged();
        }
        addMessage('bot', `Shot ${i + 1} copied — open ✏️ on an image and press 📋 Clone to use it.`);
        scrollBottom();
      });
      tdBtn.appendChild(copy);
      tr.appendChild(tdBtn);

      rows.push(tr);
      tbody.appendChild(tr);
    });
    markCurrent();
    syncButtons();
  };

  insertBtn.addEventListener('click', () => {
    if (!isLive()) return;
    videoStoryInsert(story);
    renderRows();
    notifyVideoStoryChanged();
    const ta = rows[story.cursor] && rows[story.cursor].querySelector('textarea');
    if (ta) ta.focus();
  });
  deleteBtn.addEventListener('click', () => {
    if (!isLive()) return;
    videoStoryDelete(story);
    renderRows();
    notifyVideoStoryChanged();
  });

  renderRows();
  // Keep the highlight right when ⏭ (or another copy of this table) moves the cursor or
  // changes the rows while this table is on screen.
  const onChange = () => {
    if (!bubble.isConnected) { cleanup(); return; }
    if (rows.length !== story.items.length) renderRows();
    else { markCurrent(); syncButtons(); }
  };
  const cleanup = () => document.removeEventListener('video-story-changed', onChange);
  document.addEventListener('video-story-changed', onChange);
  bubble._storyCleanup = cleanup;

  scrollBottom();
}
