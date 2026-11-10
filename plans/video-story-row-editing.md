# /video-story: insert/delete rows and click-to-set cursor

## Context
The `/video-story` table (`static/js/story.js`, `renderVideoStoryTable`) only lets you
edit cells, and the cursor (`state.videoStory.cursor`, the "current shot"; ⏭ on a video
plays `cursor + 1` via `videoStoryNext` in `utils.js`) can only be moved by 📋 or ⏭.
The user wants to add/remove shots manually and move the cursor by clicking a row.

## Behaviour
- **Click a row to set the cursor.** A click anywhere on a row that isn't a textarea or
  button makes it the current shot (highlighted with the existing `.story-current`).
  Clicking the already-current row clears the cursor to `-1`, so ⏭ starts from shot 1
  again (otherwise there'd be no way back to "nothing played yet"). The `#` cell gets
  `cursor: pointer`; tooltip on the row explains it.
- **Buttons under the table**: `➕ Insert row` and `🗑 Delete row`, styled `sel-btn`.
  - Insert adds a blank shot `{description:'', soundscape:'', music:'N/A'}` **after** the
    cursor (at the top when cursor is `-1`) and moves the cursor onto it — so the new
    row is selected and ready to type into (its description textarea is focused).
  - Delete removes the cursor row; disabled when cursor is `-1` or the story is empty.
    The cursor then moves to the preceding row (or `-1`), so the selection stays on the
    shot ⏭ last carried on from and ⏭'s next shot is the one that followed the deleted row.
- Header clip count updates after insert/delete.

## Implementation
1. **Plan file** → `plans/video-story-row-editing.md`, committed before implementation
   (repo convention).
2. **`static/js/utils.js`** — two pure helpers beside `videoStoryNext`, mutating the story
   and returning nothing interesting:
   - `videoStoryInsert(story)` — splice blank item at `cursor + 1`, set `cursor` to it.
   - `videoStoryDelete(story)` — no-op unless `0 <= cursor < items.length`; splice, set
     `cursor = cursor - 1` (min `-1`).
   - `videoStorySetCursor(story, i)` — toggle semantics above.
3. **`static/js/story.js`** — restructure `renderVideoStoryTable`:
   - Keep the outer render, but move header-count + `tbody` building into an inner
     `renderRows()` so insert/delete just rebuild the rows.
   - Row `click` handler: ignore if `e.target.closest('textarea, button')`; else
     `videoStorySetCursor` → `markCurrent` → `notifyVideoStoryChanged()`.
   - Footer `div` with the two buttons; `syncButtons()` sets Delete's `disabled`.
   - Guard every action with `state.videoStory === story` (as 📋 already does).
   - Listener hygiene: store a cleanup fn on the bubble (`bubble._storyCleanup`) and call
     it at the top of each render, so re-rendering doesn't stack `video-story-changed`
     listeners. In the `onChange` listener, if `story.items.length` differs from the
     rendered row count (another open copy of the table inserted/deleted), call
     `renderRows()`; otherwise just `markCurrent()` + `syncButtons()`.
   - Update the header help line to mention click-to-select and the buttons.
4. **`static/css/chat.css`** — `.story-table tbody tr { cursor: pointer }` (textareas keep
   their text cursor), a small `.story-actions` flex row for the buttons.
5. **`static/js/commands.js`** — extend the `/video-story` help entry text.
6. **Docs** — add bullets to the "Continuous video stories" section of `CLAUDE.md` and to
   `ADR/video-story.md`.

No server changes: `state.videoStory` is already persisted via `saveSession` on
`video-story-changed` (`chat.js:513`), and `syncStoryNextButton` refreshes ⏭ titles.

## Verification
- `node --check` on `story.js`, `utils.js`, `commands.js` (curly-quote pitfall).
- Jest (`npm run test:js`): new cases in `tests/js/utils.test.js` for insert (cursor -1 →
  top; mid → after cursor; cursor moves), delete (mid, first → -1, no-op at -1), set-cursor
  toggle.
- `./scripts/test-all` for the Python side (unchanged, sanity).
- Manual: `/video-story …`, click rows (highlight + ⏭ tooltip "Next story shot N"),
  insert/delete, reload page and `/video-story-review` to confirm persistence.
