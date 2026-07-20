// ---------------------------------------------------------------------------
// Expandable vertical chat sidebar (ChatGPT-style).
//
// Collapsed by default: a narrow icon rail with a toggle and a "new chat"
// button. Expanded: a 260px panel listing saved chats (GET /api/chats), each
// row switching into a chat (restoreSession) with per-row rename/delete.
//
// This is a presentation surface over existing logic — it reuses newChat(),
// restoreSession(), and the /api/chats REST endpoints rather than duplicating
// them.
// ---------------------------------------------------------------------------

import { escapeHtml, parseJsonResponse } from './utils.js';

export function initSidebar(deps) {
  const { newChat, restoreSession, getRecordingName, setRecordingName } = deps;

  const sidebar    = document.getElementById('sidebar');
  const toggleBtn  = document.getElementById('sidebar-toggle');
  const newBtn     = document.getElementById('sidebar-new');
  const listEl     = document.getElementById('sidebar-list');
  const backdropEl = document.getElementById('sidebar-backdrop');
  if (!sidebar || !toggleBtn || !newBtn || !listEl) return;

  const isMobile = () => window.matchMedia('(max-width: 600px)').matches;

  function isExpanded() { return !sidebar.classList.contains('collapsed'); }

  function applyToggleGlyph() {
    const expanded = isExpanded();
    toggleBtn.textContent = expanded ? '»' : '☰'; // » when open, ☰ when collapsed
    toggleBtn.title = expanded ? 'Collapse sidebar' : 'Expand sidebar';
  }

  function setCollapsed(collapsed) {
    sidebar.classList.toggle('collapsed', collapsed);
    try { localStorage.setItem('sidebar-collapsed', collapsed ? '1' : '0'); } catch (e) {}
    applyToggleGlyph();
    if (!collapsed) refreshChatList();
  }

  // Always start collapsed on load (e.g. straight after logging in),
  // regardless of any previously persisted state.
  sidebar.classList.toggle('collapsed', true);
  applyToggleGlyph();

  toggleBtn.addEventListener('click', () => setCollapsed(isExpanded()));

  newBtn.addEventListener('click', () => {
    newChat();
    // newChat resets state synchronously; refresh so the new temp chat shows.
    refreshChatList();
    if (isMobile()) setCollapsed(true);
  });

  if (backdropEl) backdropEl.addEventListener('click', () => setCollapsed(true));

  // --- Chat list -----------------------------------------------------------

  function renderEmpty() {
    listEl.innerHTML = '<div class="sidebar-empty">No saved chats yet.</div>';
  }

  function refreshChatList() {
    if (!isExpanded()) return; // no point fetching while collapsed/hidden
    fetch('/api/chats')
      .then(parseJsonResponse)
      .then(sessions => {
        if (!Array.isArray(sessions) || !sessions.length) { renderEmpty(); return; }
        const current = getRecordingName();
        listEl.innerHTML = '';
        sessions.forEach(s => listEl.appendChild(buildRow(s, current)));
      })
      .catch(() => {
        listEl.innerHTML = '<div class="sidebar-empty" style="color:#f87171">Failed to load chats.</div>';
      });
  }

  function buildRow(s, current) {
    const date = s.saved_at ? new Date(s.saved_at).toLocaleDateString() : '';
    const row = document.createElement('div');
    row.className = 'sel-row';
    row.dataset.name = s.name;

    const btn = document.createElement('button');
    btn.className = 'sel-btn' + (s.name === current ? ' current' : '');
    btn.dataset.name = s.name;
    btn.innerHTML = `<span>${escapeHtml(s.name)}</span>
      <span style="color:#475569;font-size:0.8em">${s.image_count} image(s)${date ? ' · ' + date : ''}</span>`;
    btn.addEventListener('click', () => switchTo(s.name));

    const renameBtn = document.createElement('button');
    renameBtn.className = 'sel-rename-btn';
    renameBtn.title = 'Rename chat';
    renameBtn.innerHTML = '✏';
    renameBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      startInlineRename(row, s.name);
    });

    const delBtn = document.createElement('button');
    delBtn.className = 'sel-del-btn';
    delBtn.title = 'Delete chat';
    delBtn.innerHTML = '🗑';
    delBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteChat(row, s.name, delBtn);
    });

    row.appendChild(btn);
    row.appendChild(renameBtn);
    row.appendChild(delBtn);
    return row;
  }

  function switchTo(name) {
    fetch('/api/chats/' + encodeURIComponent(name))
      .then(parseJsonResponse)
      .then(data => {
        if (data.error) throw new Error(data.error);
        restoreSession(data);
        refreshChatList();
        if (isMobile()) setCollapsed(true);
      })
      .catch(() => {});
  }

  function deleteChat(row, name, delBtn) {
    delBtn.disabled = true;
    delBtn.style.opacity = '0.4';
    fetch('/api/chats/' + encodeURIComponent(name), { method: 'DELETE' })
      .then(parseJsonResponse)
      .then(data => {
        if (data.error) throw new Error(data.error);
        row.remove();
        if (!listEl.querySelector('.sel-row')) renderEmpty();
      })
      .catch(() => {
        delBtn.disabled = false;
        delBtn.style.opacity = '';
      });
  }

  // Inline rename: swap the row's main button for a text input.
  function startInlineRename(row, name) {
    const existing = row.querySelector('.sidebar-rename-input');
    if (existing) { existing.focus(); return; }
    const btn = row.querySelector('.sel-btn');
    if (!btn) return;

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'sidebar-rename-input';
    input.value = name;
    btn.replaceWith(input);
    input.focus();
    input.select();

    let done = false;
    const cancel = () => { if (!done) { done = true; refreshChatList(); } };
    const commit = () => {
      if (done) return;
      const to = input.value.trim();
      if (!to || to === name) { cancel(); return; }
      done = true;
      input.disabled = true;
      fetch('/api/chats/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from: name, to }),
      })
        .then(parseJsonResponse)
        .then(data => {
          if (data.error) throw new Error(data.error);
          if (name === getRecordingName()) setRecordingName(data.name);
          refreshChatList();
        })
        .catch(err => {
          input.disabled = false;
          input.style.borderColor = '#ef4444';
          input.title = err.message || 'Rename failed';
          done = false;
        });
    };

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter')  { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
    input.addEventListener('blur', commit);
  }

  // External flows (auto-save, new chat, renames, deletes) announce
  // changes so the list stays fresh without polling.
  document.addEventListener('chats-changed', refreshChatList);
}
