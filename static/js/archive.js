import { escapeHtml, archiveBreadcrumb, archiveParentPath } from './utils.js';
import { state } from './state.js';
import { addMessage, clearBubble, scrollBottom, createMediaElement, deleteArchiveFile } from './dom.js';
import { openLightbox } from './lightbox.js';
import { createSlideshow } from './slideshow.js';

// The in-chat folder browser for the encrypted archive (/archive-explore).
//
// Everything archived by /archive-session & friends lands on the LUKS archive
// volume under staging/<folder>/ and is deleted from the gallery, so until now the
// only way back to it was `m` on the host. This panel reads it in place.
//
// The volume is unmounted at rest and mounted on a lease by the server (see
// archive_browse.py), which every request here renews. That is invisible from this
// side with one exception: the Close button, which returns the volume to closed —
// worth having, because /fscheck needs it unmounted.

const ROOT_LABEL = 'archive';

function fmtSize(bytes) {
  if (!bytes) return '';
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

// The house idiom for endpoints that report errors with a non-2xx status.
function getJson(url, opts) {
  return fetch(url, opts)
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) throw new Error((data && data.error) || 'Request failed');
      return data;
    });
}

export function renderArchiveBrowser(bubble) {
  let cwd = '';

  function fail(msg) {
    clearBubble(bubble);
    const err = document.createElement('div');
    err.innerHTML = `<span style="color:#f87171">⚠ ${escapeHtml(msg)}</span>`;
    bubble.appendChild(err);
    scrollBottom();
  }

  function load(path) {
    clearBubble(bubble);
    const loading = document.createElement('div');
    loading.className = 'status-text';
    loading.textContent = 'Opening the encrypted archive…';
    bubble.appendChild(loading);
    getJson('/api/archive-browse?path=' + encodeURIComponent(path || ''))
      .then(data => { cwd = data.path; render(data); })
      .catch(err => fail(err.message));
  }

  function render(data) {
    clearBubble(bubble);

    // --- breadcrumb -------------------------------------------------------
    const crumbs = document.createElement('div');
    crumbs.className = 'arch-crumbs';
    archiveBreadcrumb(data.path, ROOT_LABEL).forEach((crumb, i, all) => {
      if (i) crumbs.appendChild(document.createTextNode(' / '));
      const isLast = i === all.length - 1;
      if (isLast) {
        const here = document.createElement('span');
        here.className = 'arch-crumb-here';
        here.textContent = crumb.name;
        crumbs.appendChild(here);
      } else {
        const link = document.createElement('button');
        link.className = 'arch-crumb';
        link.textContent = crumb.name;
        link.addEventListener('click', () => load(crumb.path));
        crumbs.appendChild(link);
      }
    });
    bubble.appendChild(crumbs);

    // --- status line ------------------------------------------------------
    const statusEl = document.createElement('div');
    statusEl.className = 'status-text arch-status';
    let statusTimer = null;
    function status(msg) {
      statusEl.textContent = msg;
      clearTimeout(statusTimer);
      if (msg) statusTimer = setTimeout(() => { statusEl.textContent = ''; }, 4000);
    }

    // --- toolbar ----------------------------------------------------------
    const bar = document.createElement('div');
    bar.className = 'arch-bar';

    const slideBtn = document.createElement('button');
    slideBtn.className = 'sel-btn arch-action';
    slideBtn.innerHTML = '&#9654; Slideshow';
    slideBtn.title = 'Play every image and video in this folder and below';
    slideBtn.addEventListener('click', () => {
      slideBtn.disabled = true;
      getJson('/api/archive-browse/media?path=' + encodeURIComponent(cwd))
        .then(urls => {
          if (!urls.length) {
            status(`Nothing to play in ${cwd || ROOT_LABEL}.`);
            return;
          }
          // A new bubble, as /slideshow-* does, so the browser stays on screen
          // to navigate with once the reel is running.
          const reel = addMessage('bot', '');
          state.activeSlideshowCtrl = createSlideshow(reel, urls, {
            deleteMedia: deleteArchiveFile,
          });
        })
        .catch(err => status('⚠ ' + err.message))
        .finally(() => { slideBtn.disabled = false; });
    });
    bar.appendChild(slideBtn);

    if (data.parent !== null) {
      const upBtn = document.createElement('button');
      upBtn.className = 'sel-btn arch-action';
      upBtn.innerHTML = '<span style="color:#64748b">&lsaquo;</span> Up';
      upBtn.addEventListener('click', () => load(archiveParentPath(cwd) || ''));
      bar.appendChild(upBtn);
    }

    const closeBtn = document.createElement('button');
    closeBtn.className = 'sel-btn arch-action';
    closeBtn.textContent = 'Close archive';
    closeBtn.title = 'Unmount the encrypted volume now (needed before /fscheck)';
    closeBtn.addEventListener('click', () => {
      closeBtn.disabled = true;
      getJson('/api/archive-browse/close', { method: 'POST' })
        .then(() => {
          clearBubble(bubble);
          const done = document.createElement('div');
          done.className = 'status-text';
          done.innerHTML = 'Archive closed. Run <code>/archive-explore</code> to open it again.';
          bubble.appendChild(done);
          scrollBottom();
        })
        .catch(err => { status('⚠ ' + err.message); closeBtn.disabled = false; });
    });
    bar.appendChild(closeBtn);
    bubble.appendChild(bar);
    bubble.appendChild(statusEl);

    if (!data.dirs.length && !data.files.length) {
      status(data.path ? 'This folder is empty.' : 'The archive is empty.');
    }

    // --- folders ----------------------------------------------------------
    if (data.dirs.length) {
      const list = document.createElement('div');
      list.className = 'sel-list';
      data.dirs.forEach(dir => {
        const btn = document.createElement('button');
        btn.className = 'sel-btn';
        const count = dir.count === 1 ? '1 item' : `${dir.count} items`;
        btn.innerHTML = `&#128193; <strong style="color:#cbd5e1">${escapeHtml(dir.name)}</strong> `
          + `<span style="color:#475569">— ${count}</span>`;
        btn.addEventListener('click', () => load(dir.path));
        list.appendChild(btn);
      });
      bubble.appendChild(list);
    }

    // --- files ------------------------------------------------------------
    if (data.files.length) {
      const urls = data.files.map(f => f.url);
      const frame = document.createElement('div');
      frame.className = 'review-frame';
      const legend = document.createElement('div');
      legend.className = 'review-count';
      legend.textContent = data.files.length === 1 ? '1 file' : `${data.files.length} files`;
      frame.appendChild(legend);

      const grid = document.createElement('div');
      grid.className = 'review-grid';
      data.files.forEach(file => {
        const cell = document.createElement('div');
        cell.className = 'review-thumb';
        cell.title = `${file.name}${file.size ? ' · ' + fmtSize(file.size) : ''}`;

        const media = createMediaElement(file.url);
        if (file.is_video) {
          // Native controls, muted, metadata only — matching the review grid, so a
          // folder of clips doesn't try to buffer all of them at once.
          media.muted = true;
          media.preload = 'metadata';
        } else {
          media.addEventListener('click', () => openLightbox(file.url, urls.slice()));
        }
        cell.appendChild(media);

        const del = document.createElement('button');
        del.className = 'img-del review-del';
        del.title = 'Delete from the archive';
        del.innerHTML = '&#128465;&#xFE0E;';
        del.addEventListener('click', e => {
          e.stopPropagation();
          del.disabled = true;
          deleteArchiveFile(file.url)
            .then(() => {
              cell.remove();
              const i = urls.indexOf(file.url);
              if (i !== -1) urls.splice(i, 1);
              // The archive is the last copy, so say what went.
              status(`Deleted ${file.name}.`);
              if (!urls.length) load(cwd);
            })
            .catch(err => { status('⚠ ' + err.message); del.disabled = false; });
        });
        cell.appendChild(del);
        grid.appendChild(cell);
      });
      frame.appendChild(grid);
      bubble.appendChild(frame);
    }

    scrollBottom();
  }

  load('');
}
