# Character Art Sources

Reference downloads are kept in `references/` for local art study. Original game
characters belong to their respective owners; reference images are not shipped
with the frontend. Source URLs and credits are recorded alongside the files.

The project sprites are drawn by `pixel_renderer.py` and packed by `draw_characters.py` into
`frontend/public/assets/characters/`. Run it with the project's `llm` environment.

Gameplay limits are set in `configs/characters/<character_id>/character.json`: `max_health`,
`max_stamina`, and `max_mana` must be positive numbers. They set starting resources,
rest caps, replay validation, and the UI meters for that character. If a field is
absent or `null`, the corresponding `GameConfig` maximum applies. Changes affect
new matches; recorded replays retain their original values.

The fighters use a chunky Q-style design: big heads, short bodies, mitten hands,
simple eyes and broad color clusters. They are authored on an 80 × 64 grid and
exported at 320 × 256 using exact 4× nearest-neighbor scaling. There is no
antialiasing, realistic anatomy or fine embroidery. Each character uses a small
palette with clear silhouettes and a few identifying accessories.

Crimson Blade retains rose twin tails, a flower, a short dress and a sword.
Frost Bell retains blue hair, curved horns, a bell, a white split hem and a crystal
staff. The reference studies inform these recognizable features rather than
realistic proportions. Portraits are cropped from the same coarse artwork.

Each atlas contains 11 actions with four poses per action. Version 2 of
`animations.json` sets `scale: 1`; the foot anchor is 232 / 256, aligned with the
bottom of the new boots. Attacks, blinking, resting and hurt expressions are
drawn on the same pixel grid, and raised weapons appear in front of the hair.
`pixel-preview.png` shows eight action families.

Skill effects in `frontend/src/arena/pixelEffects.ts` share a 4px world grid:
stepped slashes, ice shards, shields, teleport rings and recovery particles.
Three sword attacks use different silhouettes; attack afterglow lasts up to
120ms into recovery. Effects are reconstructed from replay time, including
left/right projectiles and the teleport departure/arrival. `make_effects()` in
`draw_characters.py` authors separate sword and ice impacts at 32 × 32, exports
64 × 64 frames and displays them at 2× for the same 4px block size.

The Sakura Court background is authored separately in `tools/draw_arena.py`:
600 × 240 pixel geometry, exported at 1200 × 480 with nearest-neighbor scaling.
It includes flowering cherry trees, layered mountains, a shrine, stone lanterns
and a terrace aligned with the fighters' existing ground position. Both renderers
are deterministic and work offline; no image generation API is required.
