import { escapeHtml, fuzzyScore, parseJsonResponse, expandAliases, applyReplacements, deriveFaceDetailPrompt, isVideoUrl } from '../../static/js/utils.js';

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
