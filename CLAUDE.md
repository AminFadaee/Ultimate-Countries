# Working on this repo

## Running the pipeline

- Don't run a full `geography places` or `geography country CODE` to see a code change. A full run re-collects every
  place from Wikidata, Wikipedia and Commons and takes about an hour.
- After changing map drawing, river art or surroundings code, delete the affected map files and run
  `uv run geography places --maps-only` or `uv run geography country CODE --maps-only`. These reuse the saved place files
  and outlines and only redo the river and surroundings research and the missing maps. They also reuse Wikidata answers
  younger than a week from `src/geography/cache/wikidata`, which full runs write, so a second redraw barely touches the
  network.
- Try a change on a few places first, with a small script that renders them into `scratch/`, before redrawing a deck.
- Run long jobs under a memory cap so a runaway can't take the machine down:
  `systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 uv run geography ...`. Use one worker for Places.
- Never use `pkill -f` or `pgrep -f` with a pattern that appears in your own command; the shell matches itself.

## Tests

- `uv run pytest` runs in about a minute without the network. Run it instead of a full pipeline run whenever you can.
- Add a test with every fix, failing before the fix. Record new network answers with `uv run pytest --record`.
- `uv run pytest -m slow` draws real maps; `uv run pytest -m decks` checks the built decks.
