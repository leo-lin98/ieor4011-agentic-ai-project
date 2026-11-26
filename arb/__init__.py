"""Arbitrage layer: from approved pairs to executable, fee-adjusted edge.

  legs.py   a pair + its direction -> the two riskless combos (two legs each), with the payoff matrix asserted
  fees.py   both exchanges' fee schedules with their rounding, parameters from T3
  books.py  live order books for flagged markets (WebSocket on both platforms)
  edge.py   joint depth walk per combo -> executable quantity, average prices, fees, net edge
  paper.py  paper fills, T8 opportunities, T9 positions

Prices are cents (float, 0-100) everywhere except where an exchange's own formula needs dollars.
"""
