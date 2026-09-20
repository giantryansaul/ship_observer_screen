# The double board is the primary design target

The panel was built as a single 64×64 board, and that is still the only hardware that exists. Layout and art decisions are nonetheless made for the double board (128×64) first and adapted down to the single board, which stays fully supported.

64 pixels of width cannot hold a side-profile drawing and readable text side by side; 128 can. Designing for the single board first would lock the art into shapes that waste the double board.

## Consequences

- Sprites are drawn 64 wide as the primary size. That one size serves the hero art on the single board and the left half of both the 1-ship and 2-ship modes on the double board. A 32-wide set exists only for the single board's 2-ship mode.
- Both boards show the same number of vessels per page in every display mode. The double board spends its width on art and less scrolling, not on more rows, so one rotation clock serves both.
- A preview size is always available, so whichever board is not physically installed can still be seen and does not rot.
