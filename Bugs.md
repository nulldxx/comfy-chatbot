# Known Issues & Open Decisions

## Videos are excluded from the disk-scan views

**Status:** open decision (not a bug — deliberate scoping)

**Area:** image2video output handling — `select_images()` (`image_store.py`), the
slideshow, `/review-all`, `/review-today`, and session archives.

**Summary:** video outputs (`.mp4`/`.webm`) currently surface only *inline in the
chat* and in `/review-session` (which renders straight from the client-side
`sessionImages` list). The disk-scan views deliberately stay image-only:
`select_images()` filters on `IMAGE_EXTS`, so videos never reach `/review-all`,
`/review-today`, the auto-advancing slideshow, or the archive zip.

**Why it's like this:** the auto-advancing slideshow (3-second carousel) can't
sensibly page through video clips, so bulk browsing was kept to still images.

**Practical downside:** a video you generate but **don't** save in a session can
only be deleted via the session that produced it. Otherwise it lingers on disk
with no UI to find or remove it (it still counts against output storage).

**If we decide to close this:**
- Let `select_images()` return `MEDIA_EXTS` instead of `IMAGE_EXTS` so videos
  appear in `/review-all` / `/review-today` / archives.
- Filter videos out at the slideshow entry points (it can't auto-advance through
  clips) — e.g. `images.filter(u => !isVideoUrl(u))`.
- `renderReviewGrid` already renders video thumbnails, so the review grids need
  no further change.

**Decision driver:** only worth doing if we expect videos to accumulate and want
to manage/clean them up after the fact.
