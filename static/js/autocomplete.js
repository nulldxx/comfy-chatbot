import { escapeHtml, fuzzyScore } from './utils.js';
import { state } from './state.js';
import { inputEl, slashAcEl } from './dom.js';

export const SLASH_COMMANDS = [
  { cmd: '/addserver',                    desc: 'add a server  (name host:port:os)',                                                 args: ' ' },
  { cmd: '/alias-create',                 desc: 'create or update a prompt text alias  (<from> <to>)',                                args: ' ' },
  { cmd: '/alias-list',                   desc: 'list all defined prompt text aliases',                                               args: ''  },
  { cmd: '/archive-all',                  desc: 'archive every image and video to the encrypted volume (optional folder name)',      args: ' ' },
  { cmd: '/archive-session',              desc: 'archive all images and videos from this session (optional folder name)',            args: ' ' },
  { cmd: '/archive-today',                desc: 'archive images and videos generated today (optional folder name)',                   args: ' ' },
  { cmd: '/clear',                        desc: 'clear visible chat (keeps settings, prompt history & session images)',               args: ''  },
  { cmd: '/delete',                       desc: 'delete the last generated image',                                                    args: ''  },
  { cmd: '/delete-all',                   desc: 'delete every image in the output folder',                                            args: ''  },
  { cmd: '/delete-session',               desc: 'delete all images from this session',                                                args: ''  },
  { cmd: '/delete-today',                 desc: 'delete every image generated today',                                                 args: ''  },
  { cmd: '/denoise',                      desc: 'set denoise defaults for face-detail, image2image, inpainting, upscale',             args: ''  },
  { cmd: '/face-detail',                  desc: 'face-detail the last N images (default 1)',                                          args: ' ' },
  { cmd: '/face-detail-super',            desc: 'N-variation face detail: face icon shows a tile picker (1 = off)',                     args: ' ' },
  { cmd: '/face-detail-prompt',           desc: 'set the prompt the face-detail icons use',                                           args: ' ' },
  { cmd: '/face-detail-prompt-reset',     desc: 'clear the override; derive prompts again',                                           args: ''  },
  { cmd: '/face-detail-replacement',      desc: 'add a find→replace applied to face-detail prompts',                                  args: ' ' },
  { cmd: '/face-detail-replacement-reset', desc: 'clear all face-detail replacements',                                                args: ''  },
  { cmd: '/face-detail-auto',              desc: 'auto-run face-detail on every new generation',                                       args: ''  },
  { cmd: '/face-detail-auto-reset',        desc: 'stop auto-running face-detail on new generations',                                   args: ''  },
  { cmd: '/face-detail-session',          desc: 'face-detail every image from this session',                                          args: ''  },
  { cmd: '/face-detail-workflow',         desc: 'choose a face-detailer workflow (no arg = picker)',                                  args: ' ' },
  { cmd: '/face-detail-workflow-reset',   desc: 'reset the face-detailer workflow to its default',                                     args: ''  },
  { cmd: '/fscheck',                      desc: 'check & auto-repair the encrypted volumes',                                          args: ''  },
  { cmd: '/generation-add-prompt',        desc: 'append extra text/LoRAs to every image generation (no arg = clear)',                 args: ' ' },
  { cmd: '/help',                         desc: 'show available commands; add a word to filter (e.g. /help prompt)',                args: '[filter]'  },
  { cmd: '/i2i',                          desc: 'image2image the last N images (default 1)',                                          args: ' ' },
  { cmd: '/i2i-replacement',              desc: 'add a find→replace for prompt-less /i2i',                                            args: ' ' },
  { cmd: '/i2i-replacement-reset',        desc: 'clear all image2image replacements',                                                args: ''  },
  { cmd: '/i2i-set-prompt',               desc: 'set an override prompt for prompt-less /i2i',                                        args: ' ' },
  { cmd: '/i2i-set-prompt-reset',         desc: 'clear the image2image override prompt',                                              args: ''  },
  { cmd: '/i2i-workflow',                 desc: 'choose an image2image workflow (no arg = picker)',                                   args: ' ' },
  { cmd: '/i2i-workflow-reset',           desc: 'reset the image2image workflow to its default',                                       args: ''  },
  { cmd: '/i2v',                          desc: 'image2video the last N images (default 1)',                                          args: ' ' },
  { cmd: '/i2v-replacement',              desc: 'add a find→replace for prompt-less /i2v',                                            args: ' ' },
  { cmd: '/i2v-replacement-reset',        desc: 'clear all image2video replacements',                                                args: ''  },
  { cmd: '/i2v-set-prompt',               desc: 'set an override prompt for prompt-less /i2v',                                        args: ' ' },
  { cmd: '/i2v-set-prompt-reset',         desc: 'clear the image2video override prompt',                                              args: ''  },
  { cmd: '/i2v-set-ref-image',            desc: 'pin the last image as the face-ID identity reference',                              args: ''  },
  { cmd: '/i2v-set-ref-image-reset',      desc: 'clear the pinned face-ID reference image',                                        args: ''  },
  { cmd: '/i2v-workflow',                 desc: 'choose an image2video workflow (no arg = picker)',                                   args: ' ' },
  { cmd: '/i2v-workflow-reset',           desc: 'reset the image2video workflow to its default',                                       args: ''  },
  { cmd: '/image-settings',               desc: 'set image resolution & generation steps (presets, flip, use workflow default)',     args: ''  },
  { cmd: '/inpaint-workflow',             desc: 'choose an inpainting workflow (no arg = picker)',                                    args: ' ' },
  { cmd: '/inpaint-workflow-reset',       desc: 'reset the inpainting workflow to its default',                                        args: ''  },
  { cmd: '/inpainting-prompt',            desc: 'set the prompt used by the inpaint button',                                          args: ' ' },
  { cmd: '/removal-workflow',             desc: 'choose an object-removal workflow (no arg = picker)',                                 args: ' ' },
  { cmd: '/removal-workflow-reset',       desc: 'reset the removal workflow to its default',                                           args: ''  },
  { cmd: '/iterations',                   desc: 'set images generated per prompt',                                                    args: ' ' },
  { cmd: '/jobs',                         desc: 'show the last 10 server-side jobs (status, cancel, pull asset into chat)',           args: ''  },
  { cmd: '/last-sent',                    desc: 'show the last workflow sent to ComfyUI with all replacements (downloadable JSON)',     args: ''  },
  { cmd: '/lora',                         desc: 'fuzzy-find a LoRA to insert',                                                        args: ' ' },
  { cmd: '/macro-create',                 desc: 'create or update a macro (name + inline step editor)',                               args: ' ' },
  { cmd: '/macro-list',                   desc: 'list all defined macros',                                                            args: ''  },
  { cmd: '/macro-set-default',            desc: 'choose the default macro for the 🤖 image button (optionally pass a name to skip the picker)', args: '[name]'  },
  { cmd: '/multi-prompt',                 desc: 'generate images for multiple prompts (one per line)',                                args: '\n' },
  { cmd: '/purge',                        desc: 'free GPU memory on active server',                                                   args: ''  },
  { cmd: '/review',                       desc: 'grid of the last N images, oldest first',                                            args: ' ' },
  { cmd: '/review-all',                   desc: 'grid of every image (tap to view, trash to delete)',                                 args: ''  },
  { cmd: '/review-session',               desc: 'grid of this session\'s images (tap to view, trash to delete)',                      args: ''  },
  { cmd: '/review-today',                 desc: 'grid of today\'s images (tap to view, trash to delete)',                             args: ''  },
  { cmd: '/sequence',                     desc: 'generate a prompt sequence from a master prompt (Grok)',                             args: ' ' },
  { cmd: '/sequence-replacement',         desc: 'add a find→replace applied to Grok prompts',                                         args: ' ' },
  { cmd: '/sequence-replacement-reset',   desc: 'clear all sequence replacements',                                                    args: ''  },
  { cmd: '/sequence-review',              desc: 'show the last sequence\'s prompts in a grid; ▶ to generate one',                     args: ''  },
  { cmd: '/server',                       desc: 'choose a ComfyUI server',                                                            args: ''  },
  { cmd: '/settings',                     desc: 'open a menu of all settings commands',                                               args: ''  },
  { cmd: '/chat-summary',                 desc: 'show active settings (workflow, replacements, etc.)',                                args: ''  },
  { cmd: '/settings-backup',             desc: 'download a ZIP backup of all settings (macros, aliases, sessions, servers)',          args: ''  },
  { cmd: '/settings-restore',            desc: 'pop and reapply the most recent /settings-save snapshot',                            args: ''  },
  { cmd: '/settings-save',              desc: 'push a snapshot of all generation settings onto an in-memory stack',                  args: ''  },
  { cmd: '/change-password',              desc: 'change your login password (stored securely, survives restarts)',                     args: ''  },
  { cmd: '/slideshow',                    desc: 'browse the last N images, oldest first',                                             args: ' ' },
  { cmd: '/slideshow-all',                desc: 'browse every image, oldest first',                                                   args: ''  },
  { cmd: '/slideshow-reverse',            desc: 'browse every image, newest first',                                                   args: ''  },
  { cmd: '/slideshow-session',            desc: 'browse this session\'s images',                                                      args: ''  },
  { cmd: '/slideshow-today',              desc: 'browse today\'s images, oldest first',                                               args: ''  },
  { cmd: '/video-splice',                 desc: 'drag to reorder this session\'s videos, tick which to include, then ✓ to join them into one', args: ''  },
  { cmd: '/t2i-workflow',                 desc: 'choose an image generation workflow template (no arg = picker)',                     args: ' ' },
  { cmd: '/t2i-workflow-iterate',         desc: 'run a prompt against several image generation workflows',                            args: ' ' },
  { cmd: '/t2i-workflow-reset',           desc: 'reset the main generation workflow to its default',                                   args: ''  },
  { cmd: '/upscale',                      desc: 'upscale the last N images (default 1, no prompt)',                                   args: ' ' },
  { cmd: '/upscale-workflow',             desc: 'choose an upscaler workflow (no arg = picker)',                                      args: ' ' },
  { cmd: '/upscale-workflow-reset',       desc: 'reset the upscaler workflow to its default',                                         args: ''  },
  { cmd: '/video-sequence',               desc: 'like /sequence, plus per-shot action & audio for video (Grok)',                      args: ' ' },
  { cmd: '/video-settings',               desc: 'set video duration, frames, fps, resolution & audio (lock one, the others follow)',  args: ''  },
  { cmd: '/workflows',                    desc: 'table of every workflow type & its current selection; click a row to switch',       args: ''  },
];

const LORA_TRIGGER_RE = /(?:^|\s)(\/lora(?: (\S*))?)$/i;

let acMatches = [];
let acFocused = -1;
let acMode = 'cmd';
let loraTriggerStart = -1;

export function renderSlashAc() {
  slashAcEl.innerHTML = acMatches.map((c, i) =>
    `<div class="slash-ac-item${i === acFocused ? ' ac-focused' : ''}" data-idx="${i}">` +
    (acMode === 'macro'
      ? `<span class="slash-ac-cmd">#${escapeHtml(c.name)}</span>` +
        `<span class="slash-ac-desc">${c.steps.length} step(s)</span>`
      : acMode === 'lora'
      ? `<span class="slash-ac-cmd">${escapeHtml(c.name)}</span>` +
        `<span class="slash-ac-desc">${c.triggers ? escapeHtml(c.triggers) : 'strength ' + c.strength}</span>`
      : `<span class="slash-ac-cmd">${c.cmd}</span>` +
        `<span class="slash-ac-desc">${c.desc}</span>`) +
    `</div>`
  ).join('');
  slashAcEl.classList.add('open');
  const focused = slashAcEl.querySelector('.ac-focused');
  if (focused) focused.scrollIntoView({ block: 'nearest' });
}

export function hideSlashAc() {
  slashAcEl.classList.remove('open');
  slashAcEl.innerHTML = '';
  acMatches = [];
  acFocused = -1;
  acMode = 'cmd';
  loraTriggerStart = -1;
}

export function updateSlashAc() {
  const before = inputEl.value.slice(0, inputEl.selectionStart);
  const m = before.match(LORA_TRIGGER_RE);
  if (m && state.LORAS.length > 0) {
    const query = m[2] || '';
    acMode = 'lora';
    loraTriggerStart = before.length - m[1].length;
    acMatches = state.LORAS
      .map(l => ({ ...l, score: Math.max(fuzzyScore(query, l.label), fuzzyScore(query, l.name)) }))
      .filter(l => l.score >= 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 8);
    if (acMatches.length === 0) { hideSlashAc(); return; }
    acFocused = -1;
    renderSlashAc();
    return;
  }

  acMode = 'cmd';
  const val = inputEl.value;

  if (val.startsWith('#') && !val.includes(' ')) {
    const query = val.slice(1).toLowerCase();
    acMode = 'macro';
    acMatches = Object.entries(state.MACROS)
      .map(([name, steps]) => ({ name, steps, score: query ? fuzzyScore(query, name) : 0 }))
      .filter(m => !query || m.score >= 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 8);
    if (!acMatches.length) { hideSlashAc(); return; }
    acFocused = -1;
    renderSlashAc();
    return;
  }

  if (!val.startsWith('/') || val.includes(' ')) { hideSlashAc(); return; }
  const typed = val.toLowerCase();
  acMatches = SLASH_COMMANDS.filter(c => c.cmd.startsWith(typed));
  if (acMatches.length === 0 || (acMatches.length === 1 && acMatches[0].cmd === typed)) {
    hideSlashAc(); return;
  }
  acFocused = -1;
  renderSlashAc();
}

export function selectSlashAcItem(idx) {
  const c = acMatches[idx];
  if (!c) return;
  if (acMode === 'macro') {
    inputEl.value = '#' + c.name;
    hideSlashAc();
    inputEl.focus();
    inputEl.style.height = 'auto';
    return;
  }
  if (acMode === 'lora') {
    const suffix = c.triggers ? ' ' + c.triggers : '';
    const tag    = `<lora:${c.name}:${c.strength}>${suffix} `;
    const caret  = inputEl.selectionStart;
    inputEl.value = inputEl.value.slice(0, loraTriggerStart) + tag + inputEl.value.slice(caret);
    const pos = loraTriggerStart + tag.length;
    hideSlashAc();
    inputEl.focus();
    inputEl.setSelectionRange(pos, pos);
  } else {
    inputEl.value = c.cmd + c.args;
    hideSlashAc();
    inputEl.focus();
    updateSlashAc();
  }
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
}

export function tryExpandAlias() {
  if (!Object.keys(state.ALIASES).length) return;
  const val    = inputEl.value;
  const cursor = inputEl.selectionStart;
  if (cursor === 0) return;
  const sep = val[cursor - 1];
  if (sep !== ' ' && sep !== '\n') return;
  const before = val.slice(0, cursor - 1);
  const m = before.match(/(\S+)$/);
  if (!m) return;
  const word      = m[1];
  if (word.startsWith('/')) return;
  const expansion = state.ALIASES[word];
  if (expansion === undefined) return;
  const wordStart = cursor - 1 - word.length;
  inputEl.value   = val.slice(0, wordStart) + expansion + val.slice(cursor - 1);
  const newCursor = wordStart + expansion.length + 1;
  inputEl.setSelectionRange(newCursor, newCursor);
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
}

// Export acFocused accessor/mutator for keyboard handler in chat.js
export function getAcState() { return { acFocused, acMode, acMatches }; }
export function setAcFocused(v) { acFocused = v; }

// Tab completion: if an item is focused select it; otherwise complete to the
// longest common prefix of all matches. Only add the args suffix (e.g. a
// trailing space) when the prefix uniquely identifies a single command.
export function tabCompleteSlashAc() {
  if (acMode === 'lora' || acMode === 'macro' || acFocused >= 0) {
    selectSlashAcItem(acFocused >= 0 ? acFocused : 0);
    return;
  }
  if (acMatches.length === 1) {
    selectSlashAcItem(0);
    return;
  }
  // Multiple matches: fill in the longest common prefix, no args suffix.
  const cmds = acMatches.map(c => c.cmd);
  let prefix = cmds[0];
  for (let i = 1; i < cmds.length; i++) {
    while (!cmds[i].startsWith(prefix)) prefix = prefix.slice(0, -1);
  }
  inputEl.value = prefix;
  inputEl.focus();
  updateSlashAc();
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
}

slashAcEl.addEventListener('click', e => {
  const item = e.target.closest('.slash-ac-item');
  if (item) selectSlashAcItem(parseInt(item.dataset.idx, 10));
});
