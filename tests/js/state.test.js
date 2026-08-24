import { newReferences, cloneReferences, countReferenceFiles,
         REFERENCE_MAX_FILES, REFERENCE_TRACK_DEFAULT } from '../../static/js/state.js';

// ---------------------------------------------------------------------------
// /references slot helpers. A reference video is one clip with two usable tracks
// (VHS Load Video emits IMAGE and AUDIO), so it charges the 12-file cap once per
// ticked track — arithmetic that's cheap to test here and painful to debug in the UI.
// ---------------------------------------------------------------------------

describe('newReferences', () => {
  test('has the right slot counts and no legacy videoAudios group', () => {
    const r = newReferences();
    expect(r.images).toHaveLength(9);
    expect(r.videos).toHaveLength(3);
    expect(r.videoTracks).toHaveLength(3);
    expect(r.audios).toHaveLength(3);
    expect(r.videoAudios).toBeUndefined();
  });

  test('tracks default to both on', () => {
    expect(newReferences().videoTracks[0]).toEqual(REFERENCE_TRACK_DEFAULT);
  });

  test('is a factory, not a shared object', () => {
    const a = newReferences();
    const b = newReferences();
    a.images[0] = '/images/x.png';
    a.videoTracks[0].audio = false;
    expect(b.images[0]).toBeNull();
    expect(b.videoTracks[0].audio).toBe(true);
  });
});

describe('countReferenceFiles', () => {
  const withVideo = (tracks) => {
    const r = newReferences();
    r.videos[0] = '/references-file/v.mp4';
    r.videoTracks[0] = tracks;
    return r;
  };

  test('empty is zero', () => {
    expect(countReferenceFiles(newReferences())).toBe(0);
    expect(countReferenceFiles(null)).toBe(0);
  });

  test('a clip using both tracks costs 2', () => {
    expect(countReferenceFiles(withVideo({ video: true, audio: true }))).toBe(2);
  });

  test('a clip using one track costs 1', () => {
    expect(countReferenceFiles(withVideo({ video: true, audio: false }))).toBe(1);
    expect(countReferenceFiles(withVideo({ video: false, audio: true }))).toBe(1);
  });

  test('an inactive clip costs nothing', () => {
    expect(countReferenceFiles(withVideo({ video: false, audio: false }))).toBe(0);
  });

  test('track flags on an empty slot cost nothing', () => {
    const r = newReferences();
    r.videoTracks[1] = { video: true, audio: true };
    expect(countReferenceFiles(r)).toBe(0);
  });

  test('images and standalone audios cost 1 each', () => {
    const r = newReferences();
    r.images[0] = '/images/a.png';
    r.images[3] = '/images/b.png';
    r.audios[2] = '/references-file/a.mp3';
    expect(countReferenceFiles(r)).toBe(3);
  });

  test('tolerates a missing videoTracks array', () => {
    expect(countReferenceFiles({ videos: ['/references-file/v.mp4'] })).toBe(0);
  });

  test('a full budget is reachable', () => {
    const r = newReferences();
    for (let i = 0; i < 9; i++) r.images[i] = `/images/i${i}.png`;
    r.videos[0] = '/references-file/v.mp4';
    r.videoTracks[0] = { video: true, audio: true };
    r.audios[0] = '/references-file/a.mp3';
    expect(countReferenceFiles(r)).toBe(REFERENCE_MAX_FILES);
  });
});

describe('cloneReferences', () => {
  test('round-trips the current shape', () => {
    const src = newReferences();
    src.images[0] = '/images/a.png';
    src.videos[1] = '/references-file/v.mp4';
    src.videoTracks[1] = { video: false, audio: true };
    const out = cloneReferences(src);
    expect(out.images[0]).toBe('/images/a.png');
    expect(out.videos[1]).toBe('/references-file/v.mp4');
    expect(out.videoTracks[1]).toEqual({ video: false, audio: true });
  });

  test('is a deep copy', () => {
    const src = newReferences();
    src.videos[0] = '/references-file/v.mp4';
    const out = cloneReferences(src);
    out.videoTracks[0].audio = false;
    out.videos[0] = null;
    expect(src.videoTracks[0].audio).toBe(true);
    expect(src.videos[0]).toBe('/references-file/v.mp4');
  });

  test('migrates a legacy paired audio into the audio track', () => {
    const out = cloneReferences({
      videos: ['/references-file/v.mp4'],
      videoAudios: ['/references-file/a.mp3'],
    });
    expect(out.videoTracks[0]).toEqual({ video: true, audio: true });
    expect(out.videoAudios).toBeUndefined();
  });

  test('a legacy clip with no paired audio keeps audio off', () => {
    const out = cloneReferences({ videos: ['/references-file/v.mp4'] });
    expect(out.videoTracks[0]).toEqual({ video: true, audio: false });
  });

  test('an orphaned legacy audio is dropped, not resurrected', () => {
    // videoAudios[1] was set but videos[1] was not — the old table allowed that.
    // There's no clip for it to be a track of, so the file is simply lost and the
    // empty slot keeps the plain default.
    const out = cloneReferences({
      videos: [null, null],
      videoAudios: [null, '/references-file/a.mp3'],
    });
    expect(out.videos[1]).toBeNull();
    expect(out.videoAudios).toBeUndefined();
    expect(out.videoTracks[1]).toEqual(REFERENCE_TRACK_DEFAULT);
  });

  test('migrates the pre-expansion scalar slots', () => {
    const out = cloneReferences({
      images: ['/images/a.png'],
      video: '/references-file/v.mp4',
      videoAudio: '/references-file/a.mp3',
      audio: '/references-file/b.mp3',
    });
    expect(out.videos[0]).toBe('/references-file/v.mp4');
    expect(out.videoTracks[0]).toEqual({ video: true, audio: true });
    expect(out.audios[0]).toBe('/references-file/b.mp3');
  });

  test('pads and trims to the slot counts', () => {
    const out = cloneReferences({ videos: ['a.mp4', 'b.mp4', 'c.mp4', 'd.mp4'] });
    expect(out.videos).toHaveLength(3);
    expect(out.videoTracks).toHaveLength(3);
    expect(out.audios).toEqual([null, null, null]);
  });

  test('tolerates undefined', () => {
    expect(cloneReferences(undefined)).toEqual(newReferences());
  });
});
