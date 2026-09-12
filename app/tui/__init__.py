"""
Terminal UI. The only package allowed to import Textual.

It is a pure consumer of RvolEngine: it polls snapshot() on a timer and
drains events(). It never reaches into engine state, which is what keeps
the engine independent of any one UI.
"""
