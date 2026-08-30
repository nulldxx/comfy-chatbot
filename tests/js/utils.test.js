import { escapeHtml, fuzzyScore, parseJsonResponse, expandAliases, applyReplacements, upsertReplacement, deriveFaceDetailPrompt, isVideoUrl,
         fmtDuration, clampVideo, recomputeVideo, DEFAULT_VIDEO_SETTINGS, buildVideoPrompt, i2vTooltip, reorderList,
         formatFscheckResult, computeDiffBox, clampMenuPosition,
         videoOptsPayload, activeAccelerator, VIDEO_OPTIMIZATIONS, TURBO_STEPS, BASE_VIDEO_STEPS,
         splitWorkflowVariant, joinWorkflowVariant, workflowLabelHtml,
         WORKFLOW_VARIANT_SEP, progressPercent, progressCaption } from '../../static/js/utils.js';

// ---------------------------------------------------------------------------
// computeDiffBox — locates the changed (face) region for the super tile picker
// ---------------------------------------------------------------------------

// Build a w×h solid-grey RGBA buffer, optionally painting a white rectangle.
function makeImage(w, h, rect) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    data[i * 4] = data[i * 4 + 1] = data[i * 4 + 2] = 128;
    data[i * 4 + 3] = 255;
  }
  if (rect) {
    for (let y = rect.y; y < rect.y + rect.h; y++) {
      for (let x = rect.x; x < rect.x + rect.w; x++) {
        const i = (y * w + x) * 4;
        data[i] = data[i + 1] = data[i + 2] = 255;
      }
    }
  }
  return data;
}

describe('computeDiffBox', () => {
  test('identical images → null (caller falls back to full image)', () => {
    const a = makeImage(20, 20);
    const b = makeImage(20, 20);
    expect(computeDiffBox(a, b, 20, 20)).toBeNull();
  });

  test('a changed rectangle → padded box containing it', () => {
    const a = makeImage(100, 100);
    const b = makeImage(100, 100, { x: 40, y: 40, w: 20, h: 20 });
    const box = computeDiffBox(a, b, 100, 100, { pad: 0.15, minFrac: 0 });
    // The 20px change padded by 15% (3px) on each side, clamped to the canvas.
    expect(box).toEqual({ x: 37, y: 37, w: 26, h: 26 });
  });

  test('padding is clamped at the image edges', () => {
    const a = makeImage(50, 50);
    const b = makeImage(50, 50, { x: 0, y: 0, w: 10, h: 10 });
    const box = computeDiffBox(a, b, 50, 50, { pad: 0.5, minFrac: 0 });
    expect(box.x).toBe(0);
    expect(box.y).toBe(0);
    expect(box.x + box.w).toBeLessThanOrEqual(50);
  });

  test('a change smaller than minFrac is treated as noise → null', () => {
    const a = makeImage(100, 100);
    const b = makeImage(100, 100, { x: 0, y: 0, w: 1, h: 1 });
    expect(computeDiffBox(a, b, 100, 100)).toBeNull();
  });

  test('mismatched buffer sizes → null', () => {
    const a = makeImage(20, 20);
    const b = makeImage(21, 20);
    expect(computeDiffBox(a, b, 20, 20)).toBeNull();
  });

  test('a sub-threshold difference does not register', () => {
    const a = makeImage(30, 30);
    const b = makeImage(30, 30);
    // Nudge one pixel by less than the default threshold (40) across 3 channels.
    b[0] += 5; b[1] += 5; b[2] += 5;
    expect(computeDiffBox(a, b, 30, 30)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// reorderList
// ---------------------------------------------------------------------------

describe('reorderList', () => {
  test('moves an item forward', () => {
    expect(reorderList(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd']);
  });

  test('moves an item backward', () => {
    expect(reorderList(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c']);
  });

  test('from === to is a no-op copy', () => {
    const input = ['a', 'b', 'c'];
    const out = reorderList(input, 1, 1);
    expect(out).toEqual(['a', 'b', 'c']);
    expect(out).not.toBe(input);
  });

  test('does not mutate the input array', () => {
    const input = ['a', 'b', 'c'];
    reorderList(input, 0, 2);
    expect(input).toEqual(['a', 'b', 'c']);
  });

  test('out-of-range indices return an unchanged copy', () => {
    expect(reorderList(['a', 'b'], 5, 0)).toEqual(['a', 'b']);
    expect(reorderList(['a', 'b'], 0, -1)).toEqual(['a', 'b']);
  });
});

// ---------------------------------------------------------------------------
// escapeHtml
// ---------------------------------------------------------------------------

describe('escapeHtml', () => {
  test('passes through plain text unchanged', () => {
    expect(escapeHtml('hello world')).toBe('hello world');
  });

  test('escapes ampersand', () => {
    expect(escapeHtml('a & b')).toBe('a &amp; b');
  });

  test('escapes less-than', () => {
    expect(escapeHtml('<b>bold</b>')).toBe('&lt;b&gt;bold&lt;/b&gt;');
  });

  test('handles combined special characters', () => {
    expect(escapeHtml('<script>alert(1 & 2)</script>')).toBe(
      '&lt;script&gt;alert(1 &amp; 2)&lt;/script&gt;'
    );
  });

  test('coerces non-string input to string', () => {
    expect(escapeHtml(42)).toBe('42');
    expect(escapeHtml(null)).toBe('null');
  });

  test('returns empty string unchanged', () => {
    expect(escapeHtml('')).toBe('');
  });
});

// ---------------------------------------------------------------------------
// fuzzyScore
// ---------------------------------------------------------------------------

describe('fuzzyScore', () => {
  test('returns 0 for empty query', () => {
    expect(fuzzyScore('', 'anything')).toBe(0);
  });

  test('returns -1 when no subsequence match exists', () => {
    expect(fuzzyScore('xyz', 'abcde')).toBe(-1);
  });

  test('returns positive score for exact match', () => {
    expect(fuzzyScore('abc', 'abc')).toBeGreaterThan(0);
  });

  test('returns positive score for subsequence match', () => {
    expect(fuzzyScore('ace', 'abcde')).toBeGreaterThan(0);
  });

  test('consecutive match scores higher than scattered match', () => {
    const consecutive = fuzzyScore('abc', 'abcxxx');   // abc at positions 0,1,2
    const scattered   = fuzzyScore('abc', 'axbxcx');   // a,b,c with gaps
    expect(consecutive).toBeGreaterThan(scattered);
  });

  test('match at start of text scores higher than match in middle', () => {
    const fromStart  = fuzzyScore('ab', 'abcdef');
    const fromMiddle = fuzzyScore('ab', 'xyzabc');
    expect(fromStart).toBeGreaterThan(fromMiddle);
  });

  test('is case-insensitive', () => {
    expect(fuzzyScore('ABC', 'abcdef')).toBe(fuzzyScore('abc', 'abcdef'));
  });

  test('returns -1 when query is longer than text', () => {
    expect(fuzzyScore('abcdef', 'abc')).toBe(-1);
  });
});

// ---------------------------------------------------------------------------
// parseJsonResponse
// ---------------------------------------------------------------------------

function makeResponse(body, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    text: () => Promise.resolve(body),
  };
}

describe('parseJsonResponse', () => {
  test('parses valid JSON', async () => {
    const result = await parseJsonResponse(makeResponse('{"key":"value"}'));
    expect(result).toEqual({ key: 'value' });
  });

  test('parses JSON array', async () => {
    const result = await parseJsonResponse(makeResponse('[1,2,3]'));
    expect(result).toEqual([1, 2, 3]);
  });

  test('throws with snippet when ok response contains non-JSON', async () => {
    await expect(parseJsonResponse(makeResponse('<html>timeout</html>')))
      .rejects.toThrow('Server returned a non-JSON response');
  });

  test('includes snippet from non-JSON ok response', async () => {
    await expect(parseJsonResponse(makeResponse('Gateway Timeout')))
      .rejects.toThrow('Gateway Timeout');
  });

  test('throws with HTTP status when non-ok response contains non-JSON', async () => {
    await expect(parseJsonResponse(makeResponse('Not Found', { ok: false, status: 404 })))
      .rejects.toThrow('Request failed (HTTP 404)');
  });

  test('throws without snippet when body is empty', async () => {
    const err = await parseJsonResponse(makeResponse('', { ok: false, status: 500 }))
      .catch(e => e);
    expect(err.message).toBe('Request failed (HTTP 500)');
  });

  test('truncates long snippet to 120 characters', async () => {
    const longBody = 'x'.repeat(200);
    const err = await parseJsonResponse(makeResponse(longBody)).catch(e => e);
    const snippet = err.message.split(': ')[1];
    expect(snippet.length).toBeLessThanOrEqual(120);
  });
});

// ---------------------------------------------------------------------------
// expandAliases
// ---------------------------------------------------------------------------

describe('expandAliases', () => {
  test('returns text unchanged when aliases map is empty', () => {
    expect(expandAliases('hello world', {})).toBe('hello world');
  });

  test('expands a single matching word', () => {
    expect(expandAliases('ph', { ph: 'professional headshot' }))
      .toBe('professional headshot');
  });

  test('expands a word in the middle of text', () => {
    expect(expandAliases('a ph portrait', { ph: 'professional headshot' }))
      .toBe('a professional headshot portrait');
  });

  test('preserves whitespace around expanded tokens', () => {
    expect(expandAliases('before  ph  after', { ph: 'X' }))
      .toBe('before  X  after');
  });

  test('does not expand partial word matches', () => {
    expect(expandAliases('photo', { ph: 'professional headshot' }))
      .toBe('photo');
  });

  test('expands multiple different aliases in one pass', () => {
    const aliases = { ph: 'professional headshot', bw: 'black and white' };
    expect(expandAliases('a ph bw portrait', aliases))
      .toBe('a professional headshot black and white portrait');
  });

  test('does not expand words not in the alias map', () => {
    expect(expandAliases('hello world', { ph: 'professional headshot' }))
      .toBe('hello world');
  });
});

// ---------------------------------------------------------------------------
// applyReplacements
// ---------------------------------------------------------------------------

describe('applyReplacements', () => {
  test('returns prompt unchanged when replacements list is empty', () => {
    expect(applyReplacements('a cat on a mat', [])).toBe('a cat on a mat');
  });

  test('applies a single replacement', () => {
    expect(applyReplacements('a cat on a mat', [['cat', 'dog']]))
      .toBe('a dog on a mat');
  });

  test('applies multiple replacements in order', () => {
    expect(applyReplacements('hello world', [['hello', 'hi'], ['world', 'earth']]))
      .toBe('hi earth');
  });

  test('replaces all occurrences of a term', () => {
    expect(applyReplacements('cat and cat', [['cat', 'dog']]))
      .toBe('dog and dog');
  });

  test('returns prompt unchanged when no term matches', () => {
    expect(applyReplacements('no matches here', [['xyz', 'abc']]))
      .toBe('no matches here');
  });

  test('handles empty replacement target (removes the term)', () => {
    expect(applyReplacements('remove this word', [['this ', '']]))
      .toBe('remove word');
  });

  test('passes a falsy prompt through unchanged even with replacements', () => {
    expect(applyReplacements(null, [['cat', 'dog']])).toBe(null);
    expect(applyReplacements('', [['cat', 'dog']])).toBe('');
  });
});

// ---------------------------------------------------------------------------
// upsertReplacement
// ---------------------------------------------------------------------------

describe('upsertReplacement', () => {
  test('appends a new pair and reports nothing displaced', () => {
    const list = [];
    expect(upsertReplacement(list, 'cat', 'dog')).toBeNull();
    expect(list).toEqual([['cat', 'dog']]);
  });

  test('overwrites an existing pair instead of appending a duplicate', () => {
    const list = [['cat', 'dog']];
    expect(upsertReplacement(list, 'cat', 'fox')).toBe('dog');
    expect(list).toEqual([['cat', 'fox']]);
  });

  test('keeps the original position when overwriting', () => {
    const list = [['a', '1'], ['b', '2'], ['c', '3']];
    upsertReplacement(list, 'b', 'two');
    expect(list).toEqual([['a', '1'], ['b', 'two'], ['c', '3']]);
  });

  test('is case-sensitive by default', () => {
    const list = [['Cat', 'dog']];
    expect(upsertReplacement(list, 'cat', 'fox')).toBeNull();
    expect(list).toEqual([['Cat', 'dog'], ['cat', 'fox']]);
  });

  test('matches case-insensitively when asked, adopting the new casing', () => {
    const list = [['Cat', 'dog']];
    expect(upsertReplacement(list, 'cAT', 'fox', true)).toBe('dog');
    expect(list).toEqual([['cAT', 'fox']]);
  });

  test('a redefined pair actually takes effect', () => {
    const list = [];
    upsertReplacement(list, 'cat', 'dog');
    upsertReplacement(list, 'cat', 'fox');
    expect(applyReplacements('a cat on a mat', list)).toBe('a fox on a mat');
  });
});

// ---------------------------------------------------------------------------
// deriveFaceDetailPrompt
// ---------------------------------------------------------------------------

describe('deriveFaceDetailPrompt', () => {
  test('returns null for null input', () => {
    expect(deriveFaceDetailPrompt(null)).toBeNull();
  });

  test('returns null for empty string', () => {
    expect(deriveFaceDetailPrompt('')).toBeNull();
  });

  test('returns null when prompt has no LoRA tag', () => {
    expect(deriveFaceDetailPrompt('a woman in a red dress')).toBeNull();
  });

  test('uses "a face" when no subject word is present', () => {
    const result = deriveFaceDetailPrompt('landscape <lora:nature:1.0>');
    expect(result).toMatch(/^a face /);
    expect(result).toContain('<lora:nature:1.0>');
  });

  test('identifies "woman" subject', () => {
    const result = deriveFaceDetailPrompt('a woman in a park <lora:name:1.0>');
    expect(result).toMatch(/a woman's face/);
    expect(result).toContain('<lora:name:1.0>');
  });

  test('identifies "man" subject without matching inside "woman"', () => {
    const result = deriveFaceDetailPrompt('an old man <lora:x:0.8>');
    expect(result).toMatch(/a man's face/);
  });

  test('woman prompt does not produce "man" subject', () => {
    const result = deriveFaceDetailPrompt('a beautiful woman <lora:x:1.0>');
    expect(result).toMatch(/a woman's face/);
    expect(result).not.toMatch(/a man's face/);
  });

  test('includes facial expression found in prompt', () => {
    const result = deriveFaceDetailPrompt('a woman smiling <lora:x:1.0>');
    expect(result).toContain('smiling');
  });

  test('deduplicates repeated expressions', () => {
    const result = deriveFaceDetailPrompt('a woman smiling and smiling <lora:x:1.0>');
    const count = (result.match(/smiling/g) || []).length;
    expect(count).toBe(1);
  });

  test('preserves multiple LoRA tags', () => {
    const result = deriveFaceDetailPrompt('a girl <lora:a:1.0> <lora:b:0.5>');
    expect(result).toContain('<lora:a:1.0>');
    expect(result).toContain('<lora:b:0.5>');
  });

  test('preserves LoRA name and strength verbatim', () => {
    const result = deriveFaceDetailPrompt('a woman <lora:my-model/detail:0.75>');
    expect(result).toContain('<lora:my-model/detail:0.75>');
  });
});

// ---------------------------------------------------------------------------
// isVideoUrl
// ---------------------------------------------------------------------------

describe('isVideoUrl', () => {
  test('matches video extensions', () => {
    expect(isVideoUrl('/images/20240101_clip.mp4')).toBe(true);
    expect(isVideoUrl('/images/20240101_clip.webm')).toBe(true);
  });

  test('is case-insensitive', () => {
    expect(isVideoUrl('/images/CLIP.MP4')).toBe(true);
  });

  test('rejects image extensions', () => {
    expect(isVideoUrl('/images/pic.png')).toBe(false);
    expect(isVideoUrl('/images/anim.gif')).toBe(false);
    expect(isVideoUrl('/images/anim.webp')).toBe(false);
  });

  test('ignores a trailing query string or fragment', () => {
    expect(isVideoUrl('/images/clip.mp4?v=2')).toBe(true);
    expect(isVideoUrl('/images/clip.webm#t=1')).toBe(true);
  });

  test('does not match the extension mid-path', () => {
    expect(isVideoUrl('/images/mp4-thumbnail.png')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Video settings math (fmtDuration / clampVideo / recomputeVideo)
// ---------------------------------------------------------------------------

describe('fmtDuration', () => {
  test('drops the decimal for whole seconds', () => {
    expect(fmtDuration(5)).toBe('5');
    expect(fmtDuration(5.0)).toBe('5');
  });

  test('keeps one decimal otherwise', () => {
    expect(fmtDuration(5.2)).toBe('5.2');
    expect(fmtDuration(5.24)).toBe('5.2');
  });
});

describe('clampVideo', () => {
  test('rounds frames and fps to integers', () => {
    expect(clampVideo('frames', 124.6)).toBe(125);
    expect(clampVideo('fps', 24.4)).toBe(24);
  });

  test('rounds duration to one decimal', () => {
    expect(clampVideo('duration', 5.24)).toBe(5.2);
  });

  test('clamps to the configured limits', () => {
    expect(clampVideo('fps', 999)).toBe(60);
    expect(clampVideo('fps', 0)).toBe(1);
    expect(clampVideo('frames', 99999)).toBe(1000);
  });

  test('snaps video width/height to a multiple of 16', () => {
    expect(clampVideo('width', 1280)).toBe(1280);   // already a multiple of 16
    expect(clampVideo('width', 1290)).toBe(1296);   // rounds up to nearest 16
    expect(clampVideo('height', 727)).toBe(720);    // rounds down to nearest 16
  });

  test('clamps video width/height to range and stays a multiple of 16', () => {
    expect(clampVideo('width', 5000)).toBe(2048);   // max, multiple of 16
    expect(clampVideo('height', 1)).toBe(64);       // min, multiple of 16
  });
});

describe('recomputeVideo', () => {
  test('lock fps: editing duration recomputes frames', () => {
    const s = { duration: 4, frames: 125, fps: 25 };
    recomputeVideo(s, 'fps', 'duration');
    expect(s.fps).toBe(25);
    expect(s.frames).toBe(100);   // 4 × 25
    expect(s.duration).toBe(4);
  });

  test('lock fps: editing frames recomputes duration', () => {
    const s = { duration: 5, frames: 200, fps: 25 };
    recomputeVideo(s, 'fps', 'frames');
    expect(s.fps).toBe(25);
    expect(s.frames).toBe(200);
    expect(s.duration).toBe(8);   // 200 / 25
  });

  test('lock duration: editing fps recomputes frames', () => {
    const s = { duration: 5, frames: 125, fps: 30 };
    recomputeVideo(s, 'duration', 'fps');
    expect(s.duration).toBe(5);
    expect(s.fps).toBe(30);
    expect(s.frames).toBe(150);   // 5 × 30
  });

  test('lock frames: editing fps recomputes duration', () => {
    const s = { duration: 5, frames: 120, fps: 24 };
    recomputeVideo(s, 'frames', 'fps');
    expect(s.frames).toBe(120);
    expect(s.fps).toBe(24);
    expect(s.duration).toBe(5);   // 120 / 24
  });

  test('the locked value is never changed', () => {
    const s = { duration: 5, frames: 125, fps: 25 };
    recomputeVideo(s, 'frames', 'fps');
    expect(s.frames).toBe(125);
  });

  test('editing the locked value is a no-op', () => {
    const s = { duration: 5, frames: 125, fps: 25 };
    recomputeVideo(s, 'fps', 'fps');
    expect(s).toEqual({ duration: 5, frames: 125, fps: 25 });
  });

  test('snaps back when a derived value hits a limit', () => {
    // lock fps=60, push duration high so frames clamps at 1000; duration then
    // snaps to 1000/60 ≈ 16.7 to stay consistent with the clamped frame count.
    const s = { duration: 60, frames: 125, fps: 60 };
    recomputeVideo(s, 'fps', 'duration');
    expect(s.fps).toBe(60);
    expect(s.frames).toBe(1000);
    expect(s.duration).toBe(16.7);
  });

  test('default settings are self-consistent (frames = duration × fps)', () => {
    const { duration, frames, fps } = DEFAULT_VIDEO_SETTINGS;
    expect(frames).toBe(duration * fps);
  });
});

// ---------------------------------------------------------------------------
// buildVideoPrompt
// ---------------------------------------------------------------------------

describe('buildVideoPrompt', () => {
  test('returns base unchanged when meta is null (backward compatible)', () => {
    expect(buildVideoPrompt('a cat on a wall', null)).toBe('a cat on a wall');
  });

  test('returns base unchanged when meta is undefined', () => {
    expect(buildVideoPrompt('a cat on a wall', undefined)).toBe('a cat on a wall');
  });

  test('returns base unchanged when meta has empty fields', () => {
    expect(buildVideoPrompt('a cat', { action: '', audio: '' })).toBe('a cat');
  });

  test('folds in action and audio in the documented format', () => {
    expect(buildVideoPrompt('a cat on a wall', { action: 'it leaps down', audio: 'a meow' }))
      .toBe('a cat on a wall. it leaps down. Audio: a meow');
  });

  test('includes action only when audio is missing', () => {
    expect(buildVideoPrompt('a cat', { action: 'it leaps down', audio: '' }))
      .toBe('a cat. it leaps down');
  });

  test('includes audio only when action is missing', () => {
    expect(buildVideoPrompt('a cat', { action: '', audio: 'a meow' }))
      .toBe('a cat. Audio: a meow');
  });

  test('trims whitespace around fields', () => {
    expect(buildVideoPrompt('a cat', { action: '  it leaps  ', audio: '  a meow ' }))
      .toBe('a cat. it leaps. Audio: a meow');
  });

  test('drops the Audio segment when includeAudio is false', () => {
    expect(buildVideoPrompt('a cat on a wall', { action: 'it leaps down', audio: 'a meow' }, false))
      .toBe('a cat on a wall. it leaps down');
  });

  test('keeps action when audio is suppressed and base has no action', () => {
    expect(buildVideoPrompt('a cat', { action: '', audio: 'a meow' }, false))
      .toBe('a cat');
  });

  test('includes audio when includeAudio defaults to true', () => {
    expect(buildVideoPrompt('a cat', { action: '', audio: 'a meow' }))
      .toBe('a cat. Audio: a meow');
  });
});

describe('i2vTooltip', () => {
  test('returns the plain label when meta is null', () => {
    expect(i2vTooltip(null)).toBe('Image to video');
  });

  test('returns the plain label when meta is undefined', () => {
    expect(i2vTooltip(undefined)).toBe('Image to video');
  });

  test('returns the plain label when meta has empty fields', () => {
    expect(i2vTooltip({ action: '', audio: '' })).toBe('Image to video');
  });

  test('appends action and audio when both present', () => {
    expect(i2vTooltip({ action: 'it leaps down', audio: 'a meow' }))
      .toBe('Image to video: it leaps down, a meow');
  });

  test('appends action only when audio is missing', () => {
    expect(i2vTooltip({ action: 'it leaps down', audio: '' }))
      .toBe('Image to video: it leaps down');
  });

  test('appends audio only when action is missing', () => {
    expect(i2vTooltip({ action: '', audio: 'a meow' }))
      .toBe('Image to video: a meow');
  });

  test('trims whitespace around fields', () => {
    expect(i2vTooltip({ action: '  it leaps  ', audio: '  a meow ' }))
      .toBe('Image to video: it leaps, a meow');
  });
});

// ---------------------------------------------------------------------------
// formatFscheckResult
// ---------------------------------------------------------------------------

describe('formatFscheckResult', () => {
  test('not configured', () => {
    expect(formatFscheckResult({ configured: false }))
      .toEqual({ icon: '—', label: 'not configured', tone: 'muted' });
  });

  test('configured but not checked yet (output, pre-first-check)', () => {
    expect(formatFscheckResult({ available: false }).label).toBe('not checked yet');
  });

  test('volume not yet provisioned (skipped)', () => {
    expect(formatFscheckResult({ ok: true, skipped: 'volume absent', clean: true }).label)
      .toBe('volume not yet created');
  });

  test('clean filesystem', () => {
    const f = formatFscheckResult({ ok: true, clean: true, corrected: false, uncorrected: false });
    expect(f).toEqual({ icon: '✓', label: 'clean', tone: 'ok' });
  });

  test('errors repaired', () => {
    const f = formatFscheckResult({ ok: true, clean: false, corrected: true, uncorrected: false });
    expect(f.tone).toBe('warn');
    expect(f.label).toMatch(/repaired/);
  });

  test('uncorrected wins over corrected when both set', () => {
    const f = formatFscheckResult({ ok: true, corrected: true, uncorrected: true });
    expect(f.tone).toBe('error');
    expect(f.label).toMatch(/problems remain/);
  });

  test('check failed surfaces the error message', () => {
    const f = formatFscheckResult({ ok: false, error: 'agent unavailable' });
    expect(f.tone).toBe('error');
    expect(f.label).toMatch(/agent unavailable/);
  });

  test('null / undefined is handled without throwing', () => {
    expect(formatFscheckResult(null).tone).toBe('muted');
    expect(formatFscheckResult(undefined).tone).toBe('muted');
  });
});


// ---------------------------------------------------------------------------
// clampMenuPosition — keeps the media right-click menu inside the viewport.
// The pointer can be anywhere, including the bottom-right corner, where a menu
// drawn at the click point would spill off-screen.
// ---------------------------------------------------------------------------

describe('clampMenuPosition', () => {
  const VW = 1000, VH = 800, W = 200, H = 150;

  test('a click with room to spare uses the click point', () => {
    expect(clampMenuPosition(100, 120, W, H, VW, VH)).toEqual({ left: 100, top: 120 });
  });

  test('a click near the right edge pulls the menu back inside', () => {
    const { left } = clampMenuPosition(950, 100, W, H, VW, VH);
    expect(left + W).toBeLessThanOrEqual(VW);
  });

  test('a click near the bottom edge pulls the menu back inside', () => {
    const { top } = clampMenuPosition(100, 780, W, H, VW, VH);
    expect(top + H).toBeLessThanOrEqual(VH);
  });

  test('the bottom-right corner clamps on both axes at once', () => {
    expect(clampMenuPosition(1000, 800, W, H, VW, VH)).toEqual({ left: 792, top: 642 });
  });

  test('never returns a negative coordinate', () => {
    // A menu taller/wider than the viewport pins to the top-left rather than
    // being pushed off the opposite edge.
    const { left, top } = clampMenuPosition(10, 10, 2000, 2000, VW, VH);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(top).toBeGreaterThanOrEqual(0);
  });

  test('a click at the origin keeps the margin', () => {
    expect(clampMenuPosition(0, 0, W, H, VW, VH)).toEqual({ left: 8, top: 8 });
  });

  test('the margin is configurable', () => {
    expect(clampMenuPosition(0, 0, W, H, VW, VH, 20)).toEqual({ left: 20, top: 20 });
  });
});

// ---------------------------------------------------------------------------
// videoOptsPayload — the /video-settings optimisation toggles on the wire
// ---------------------------------------------------------------------------

describe('videoOptsPayload', () => {
  test('every optimisation key is present, never just the off ones', () => {
    // The server reads an absent key as "on", so an omitted off flag would invert.
    const out = videoOptsPayload(DEFAULT_VIDEO_SETTINGS);
    expect(Object.keys(out).sort()).toEqual(VIDEO_OPTIMIZATIONS.map(o => o.key).sort());
  });

  test('every non-accelerator optimisation mirrors its default flag', () => {
    // Not "all on": a new optimisation may ship off until it has been rendered.
    const out = videoOptsPayload(DEFAULT_VIDEO_SETTINGS);
    VIDEO_OPTIMIZATIONS.filter(o => !o.steps).forEach(({ key, stateKey }) => {
      expect(out[key]).toBe(DEFAULT_VIDEO_SETTINGS[stateKey] !== false);
    });
  });

  test('the 8-step accelerators are the default, not turbo', () => {
    const out = videoOptsPayload(DEFAULT_VIDEO_SETTINGS);
    expect(out).toMatchObject({ turbo: false, accel8fl: true, accel8ref: true });
  });

  test('an off flag is reported as false', () => {
    const out = videoOptsPayload({ ...DEFAULT_VIDEO_SETTINGS, optSol: false });
    expect(out).toEqual({
      turbo: false, accel8fl: true, accel8ref: true,
      cache: true, sage: true, sol: false, kitchen: false, spectrum: true,
    });
  });

  test('a pre-upgrade settings object resolves to the turbo LoRA it chose', () => {
    // Old saved sessions carry an explicit optTurbo:true and no 8-step keys at all;
    // absent reads as on, so all three accelerators would otherwise stack.
    // Every non-accelerator key is absent from that object, and absent reads as on
    // — including ones that ship off by default, which is the documented behaviour.
    const out = videoOptsPayload({ duration: 5, optTurbo: true });
    expect(out).toMatchObject({ turbo: true, accel8fl: false, accel8ref: false });
    VIDEO_OPTIMIZATIONS.filter(o => !o.steps).forEach(({ key }) => {
      expect(out[key]).toBe(true);
    });
  });

  test('never sends two accelerators of different step counts', () => {
    [DEFAULT_VIDEO_SETTINGS, { duration: 5 }, undefined,
     { optTurbo: true, optAccel8Fl: true, optAccel8Ref: true }].forEach(vs => {
      const on = VIDEO_OPTIMIZATIONS.filter(o => o.steps && videoOptsPayload(vs)[o.key]);
      expect(new Set(on.map(o => o.steps)).size).toBeLessThanOrEqual(1);
    });
  });

  test('DEFAULT_VIDEO_SETTINGS carries a flag for every declared optimisation', () => {
    VIDEO_OPTIMIZATIONS.forEach(({ stateKey }) => {
      expect(typeof DEFAULT_VIDEO_SETTINGS[stateKey]).toBe('boolean');
    });
  });

  test('every optimisation carrying a step count also carries a hint', () => {
    expect(VIDEO_OPTIMIZATIONS.filter(o => o.hint).map(o => o.key))
      .toEqual(VIDEO_OPTIMIZATIONS.filter(o => o.steps).map(o => o.key));
    expect(TURBO_STEPS).toBeLessThan(BASE_VIDEO_STEPS);
    VIDEO_OPTIMIZATIONS.filter(o => o.steps).forEach(({ steps }) => {
      expect(steps).toBeLessThan(BASE_VIDEO_STEPS);
    });
  });
});

// ---------------------------------------------------------------------------
// activeAccelerator — which distillation LoRA is actually in force
// ---------------------------------------------------------------------------

describe('activeAccelerator', () => {
  test('the defaults resolve to an 8-step accelerator', () => {
    expect(activeAccelerator(DEFAULT_VIDEO_SETTINGS).steps).toBe(8);
  });

  test('turbo wins when a pre-upgrade session stacks it with the absent 8-step keys', () => {
    expect(activeAccelerator({ optTurbo: true }).key).toBe('turbo');
  });

  test('returns null when every accelerator is off', () => {
    const off = { ...DEFAULT_VIDEO_SETTINGS };
    VIDEO_OPTIMIZATIONS.filter(o => o.steps).forEach(({ stateKey }) => { off[stateKey] = false; });
    expect(activeAccelerator(off)).toBeNull();
  });

  test('either 8-step variant alone resolves to 8 steps', () => {
    const base = { ...DEFAULT_VIDEO_SETTINGS, optAccel8Ref: false };
    expect(activeAccelerator(base).key).toBe('accel8fl');
    expect(activeAccelerator({ ...DEFAULT_VIDEO_SETTINGS, optAccel8Fl: false }).key).toBe('accel8ref');
  });
});

// ---------------------------------------------------------------------------
// Workflow model variants — the "<workflow>@<model>" wire name
// ---------------------------------------------------------------------------

describe('splitWorkflowVariant / joinWorkflowVariant', () => {
  test('splits a suffixed name into workflow and model', () => {
    expect(splitWorkflowVariant('h3/minimax@fp16')).toEqual({ name: 'h3/minimax', variant: 'fp16' });
  });

  test('an unsuffixed name has no variant', () => {
    expect(splitWorkflowVariant('h3/minimax')).toEqual({ name: 'h3/minimax', variant: null });
  });

  test('splits on the last separator, so a name containing one survives', () => {
    expect(splitWorkflowVariant('odd@name@fp16')).toEqual({ name: 'odd@name', variant: 'fp16' });
  });

  test('an empty half is not a variant', () => {
    expect(splitWorkflowVariant('a@')).toEqual({ name: 'a@', variant: null });
    expect(splitWorkflowVariant('@b')).toEqual({ name: '@b', variant: null });
    expect(splitWorkflowVariant('')).toEqual({ name: '', variant: null });
    expect(splitWorkflowVariant(null)).toEqual({ name: '', variant: null });
  });

  test('the default variant joins to the BARE name, keeping the old wire format', () => {
    expect(joinWorkflowVariant('wf', null)).toBe('wf');
    expect(joinWorkflowVariant('wf', '')).toBe('wf');
  });

  test('round-trips a picked model', () => {
    const wire = joinWorkflowVariant('h3/minimax', 'fp16');
    expect(wire).toBe(`h3/minimax${WORKFLOW_VARIANT_SEP}fp16`);
    expect(splitWorkflowVariant(wire)).toEqual({ name: 'h3/minimax', variant: 'fp16' });
  });
});

describe('workflowLabelHtml', () => {
  test('renders a bare name with no model half', () => {
    expect(workflowLabelHtml('h3/minimax')).toBe('h3/minimax');
  });

  test('renders the model dimmed after the workflow', () => {
    const html = workflowLabelHtml('h3/minimax@fp16');
    expect(html).toContain('h3/minimax');
    expect(html).toContain('fp16');
    expect(html).toContain('color:#64748b');
  });

  test('escapes both halves', () => {
    // Both go through escapeHtml, which covers the markup-significant characters.
    expect(workflowLabelHtml('a<b@c>d')).toBe(
      'a&lt;b <span style="color:#64748b;font-size:0.85em">@ c&gt;d</span>');
  });
});

// ---------------------------------------------------------------------------
// progressPercent / progressCaption — the {type:"tick"} progress payload
// ---------------------------------------------------------------------------

describe('progressPercent', () => {
  test('reads a percentage', () => {
    expect(progressPercent({ type: 'tick', percent: 42.5 })).toBe(42.5);
  });

  test('a bare tick has none, so the marquee stays', () => {
    // What a run whose ComfyUI feed never connected sends.
    expect(progressPercent({ type: 'tick' })).toBeNull();
    expect(progressPercent(null)).toBeNull();
    expect(progressPercent({ percent: '80' })).toBeNull();
    expect(progressPercent({ percent: NaN })).toBeNull();
  });

  test('clamps out-of-range values', () => {
    expect(progressPercent({ percent: -5 })).toBe(0);
    expect(progressPercent({ percent: 150 })).toBe(100);
  });

  test('zero is a percentage, not an absence', () => {
    expect(progressPercent({ percent: 0 })).toBe(0);
  });
});

describe('progressCaption', () => {
  test('names the phase and the step within it', () => {
    expect(progressCaption({ phase: 'Sampling', step: 8, steps: 20,
                             node_index: 2, node_total: 4 }))
      .toBe('Sampling — step 8/20 · node 2/4');
  });

  test('a single-step node shows no step counter', () => {
    expect(progressCaption({ phase: 'VAE Decode', step: 1, steps: 1,
                             node_index: 3, node_total: 4 }))
      .toBe('VAE Decode · node 3/4');
  });

  test('node position alone is still useful', () => {
    expect(progressCaption({ node_index: 3, node_total: 9 })).toBe('node 3/9');
  });

  test('node index never exceeds the total', () => {
    expect(progressCaption({ node_index: 5, node_total: 4 })).toBe('node 4/4');
  });

  test('falls back to the queue depth before execution starts', () => {
    expect(progressCaption({ queue: 2 })).toBe('Waiting — 2 ahead in queue');
  });

  test('an empty queue is not worth saying', () => {
    expect(progressCaption({ queue: 0 })).toBeNull();
  });

  test('a bare tick captions nothing', () => {
    expect(progressCaption({ type: 'tick' })).toBeNull();
    expect(progressCaption(null)).toBeNull();
  });
});
