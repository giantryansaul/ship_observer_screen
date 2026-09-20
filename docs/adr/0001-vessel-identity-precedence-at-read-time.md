# Vessel identity precedence is applied at read time

About a third of vessels never deliver static data, so a vessel's identity is assembled from several sources that can disagree: its own broadcast, lookups and overrides. We store each source's answer untouched, side by side, and merge them only when the identity is read, in the order override → specific broadcast type → looked-up type → vague broadcast type (0 and the 90s "other" codes).

Merging at write time would be simpler to query, but it destroys the losing answer. Keeping every answer means the precedence order can change, a bad source can be distrusted, and a rejected lookup can be re-examined, all without a migration or a re-fetch.

## Consequences

- A lookup never overwrites what a vessel said about itself, and the vessel's own later broadcast never erases a lookup.
- Overrides live in a checked-in file, not the database, so they are reviewable and survive a wiped database. The database holds only machine-learned facts.
