"""
Live streaming RVOL engine.

Nothing in this package may import UI code (Textual or any
other front end). The engine owns state and math; UIs attach to it by polling
`RvolEngine.snapshot()` and draining `RvolEngine.events()`.
"""
