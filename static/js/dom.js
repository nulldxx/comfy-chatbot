import { isVideoUrl } from './utils.js';
import { state } from './state.js';

export const messagesEl = document.getElementById('messages');
export const inputEl    = document.getElementById('prompt-input');
export const sendBtn    = document.getElementById('send-btn');
export const slashAcEl  = document.getElementById('slash-ac');

export function scrollBottom() { messagesEl.scrollTop = messagesEl.scrollHeight; }

export function addMessage(role, contentHtml, rawText) {
  const wrap   = document.createElement('div');
  wrap.className = `message ${role}`;
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? 'You' : 'AI';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = contentHtml;
  if (role === 'user' && rawText != null) {
    bubble.dataset.prompt = rawText;
    const icon = document.createElement('span');
    icon.className = 'edit-icon';
    icon.textContent = '✏';
    bubble.appendChild(icon);
  }
  const close = document.createElement('button');
  close.className = 'msg-close';
  close.title = 'Dismiss';
  close.innerHTML = '&#10005;';
  close.addEventListener('click', e => {
    e.stopPropagation();
    wrap.remove();
    document.dispatchEvent(new CustomEvent('message-dismissed'));
  });
  bubble.appendChild(close);
  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  scrollBottom();
  return bubble;
}

// Empties a bubble's content while preserving the .msg-close dismiss button
// that addMessage() appended, so grid renderers that rebuild a bubble don't
// strip away its ✕.
export function clearBubble(bubble) {
  const close = bubble.querySelector('.msg-close');
  bubble.innerHTML = '';
  if (close) bubble.appendChild(close);
}

export function createMediaElement(url, { autoplay = false } = {}) {
  if (isVideoUrl(url)) {
    const video = document.createElement('video');
    video.src = url;
    video.controls = true;
    video.loop = true;
    video.playsInline = true;
    if (autoplay) { video.muted = true; video.autoplay = true; }
    return video;
  }
  const img = document.createElement('img');
  img.src = url;
  img.alt = 'Generated image';
  return img;
}

export function deleteImageFile(url) {
  const filename = url.split('/').pop();
  return fetch('/api/images/' + encodeURIComponent(filename), { method: 'DELETE' })
    .then(r => r.json().then(data => {
      if (!r.ok && r.status !== 404) throw new Error(data.error || 'Delete failed');
    }));
}

export function removeImageFromChat(url) {
  messagesEl.querySelectorAll('.img-wrap img, .img-wrap video').forEach(media => {
    if (media.getAttribute('src') === url) media.closest('.img-wrap').remove();
  });
  const i = state.sessionImages.indexOf(url);
  if (i !== -1) state.sessionImages.splice(i, 1);
  delete state.imagePrompts[url];
  delete state.imageMasks[url];
  delete state.imageVideoMeta[url];
}
