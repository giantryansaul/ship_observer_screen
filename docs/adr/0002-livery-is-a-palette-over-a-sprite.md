# Livery is a palette applied to a sprite, not a logo

The reference for the display is FlightWall, which shows a downsampled airline logo beside each flight. We deliberately do not do the ship equivalent. An operator is shown by painting the vessel's silhouette in that operator's livery: sprites are indexed images whose colours are named parts (hull, superstructure, funnel, deck/cargo, accent), a category supplies the default colours, and an operator overrides some of them.

A ship's recognisable identity is its hull and funnel colours, not a wordmark, and a wordmark shrunk to 16 pixels is just illegible text. A palette also makes one drawing serve every operator, degrades to the plain category colours when the operator is unknown, and keeps trademarked artwork out of a public repository.

## Considered options

- **Downsampled real logos, as FlightWall does**: rejected for the reasons above. A gitignored local logo directory remains supported for anyone who wants real marks on their own wall.
- **A full hand-drawn sprite per operator**: kept only as an escape hatch for a vessel that deserves its own art. As the default it multiplies drawing work by the number of operators.
