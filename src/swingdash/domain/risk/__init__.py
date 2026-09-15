"""
Risk management and position sizing for long, cash-market trades.

Pure: prices, capital and settings in; quantities, costs and warnings out.
Funding is a parameter (normal delivery today) so margin trading (MTF) can
be added as another `Funding` without changing the sizing rules.
"""
