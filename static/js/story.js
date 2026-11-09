// /video-story — the review/edit table for a continuous story of video clips.
//
// state.videoStory = { master, clipSeconds, items: [{description, soundscape, music}],
// cursor }. Each row's 📋 copies that row into state.lastVideoMeta — exactly what the
// metadata editor's Clone button reads — and moves the story cursor to it, so the ⏭
// button on a video (chat.js) carries on from the following row.
//
// Every change dispatches 'video-story-changed'; chat.js listens to it to save the
// session and refresh the ⏭ buttons, so this module needs nothing from chat.js.

import { escapeHtml } from './utils.js';
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
  const story = state.videoStory;
  bubble.innerHTML = '';
  if (!story) return;

  const header = document.createElement('div');
  header.innerHTML =
    `<strong>Video story</strong> — ${story.items.length} clip(s) of about ${escapeHtml(fmtSeconds(story.clipSeconds))}s` +
    `<div style="color:#94a3b8;font-size:0.85rem;margin-top:2px">${escapeHtml(story.master || '')}</div>` +
    '<div style="color:#64748b;font-size:0.8rem;margin-top:4px">Edit any cell. 📋 copies a row for the metadata editor\'s Clone button and makes it the current shot; ⏭ on a video cuts its last frame and starts the next shot.</div>';
  bubble.appendChild(header);

  const table = document.createElement('table');
  table.className = 'story-table';
  table.innerHTML = '<thead><tr><th>#</th><th>Description</th><th>Soundscape</th><th></th></tr></thead>';
  const tbody = document.createElement('tbody');
  table.appendChild(tbody);

  const rows = [];
  const markCurrent = () => {
    rows.forEach((tr, i) => tr.classList.toggle('story-current', i === story.cursor));
  };

  story.items.forEach((item, i) => {
    const tr = document.createElement('tr');

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
      // Re-read state: a newer /video-story may have replaced the story this table shows.
      if (state.videoStory === story) {
        story.cursor = i;
        markCurrent();
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
  // Keep the highlight right when ⏭ moves the cursor while this table is on screen.
  const onChange = () => {
    if (!bubble.isConnected) { document.removeEventListener('video-story-changed', onChange); return; }
    markCurrent();
  };
  document.addEventListener('video-story-changed', onChange);

  bubble.appendChild(table);
  scrollBottom();
}
