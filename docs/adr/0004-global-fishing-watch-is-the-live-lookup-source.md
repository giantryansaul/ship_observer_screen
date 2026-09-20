# Global Fishing Watch is the live lookup source

A vessel that needs a lookup is almost always known by MMSI and name alone: no IMO number, no call sign, no dimensions. Most are foreign deep-sea ships. Global Fishing Watch's vessel API is the one free source that can be searched by MMSI or name, covers every flag, and permits caching, so it is the only source queried live.

This binds the project to GFW's terms: non-commercial use only (CC BY-NC 4.0), and a visible "Powered by Global Fishing Watch" credit, which is shown on the debug page. Anyone taking this project commercial must replace the source.

## Considered options

- **Equasis**: the best free owner data, but its terms forbid storing the data or automating queries. Manual curiosity lookups only. Never automate it.
- **ITU MARS**: a manual web form with no API.
- **VesselFinder, MarineTraffic, Datalastic**: hundreds of euros a year or more, and their sites forbid scraping.
- **FCC ship licences, NOAA MarineCadastre archives, a Wikidata bulk pull**: free and legitimate bulk sources, deferred rather than rejected. FCC covers only US-flagged vessels, which are not the ones going unidentified. Revisit if GFW leaves a measurable gap.
