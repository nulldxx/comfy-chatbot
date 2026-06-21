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
  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  scrollBottom();
  return bubble;
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
