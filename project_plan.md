# Project Plan: Cross-Platform Prediction-Market Arbitrage

## Meeting Discussion Points

- State your question in one sentence, and say what result would answer it.
- Show your data access working: an actual pull, not a plan to get access.
- Sketch your agent architecture: what the agent decides, which tools it calls, and where the numbers come from.
- Name the past project you build on and what is new in yours.
- Confirm who owns which component.
- Say what you would cut first if you fall behind.

---

## 1. Question

**Are there cross-market arbitrage opportunities in prediction markets (Polymarket and Kalshi) that an agent can profitably take advantage of?**

## 2. Data Access

- Riddhi's work (live pull demo)

## 3. Agent Architecture

This is closer to a **workflow** than a fully autonomous agent: most steps are fixed pipeline stages, with LLM calls at the matching and verification stages and an agent layer on top for answering questions.

| # | Stage | Component | What it does |
|---|-------|-----------|--------------|
| 1 | Retrieve markets | API functions | Pull market descriptions, rules, and order books from Kalshi and Polymarket. |
| 2 | Find candidates | Embedding function | Rank markets that may describe the same event. |
| 3 | Propose matches | LLM | Explain which outcomes are equivalent or complementary and identify the required contracts. |
| 4 | Verify matches | Code + LLM judge | Code checks numbers and boundaries; a second LLM challenges the match and compares the rules. Unclear cases go to human review. |
| 5 | Calculate opportunities | Pricing function | Use order-book prices, quantities, and fee schedules to calculate the available profit. |
| 6 | Monitor and paper trade | Programmatic workflow | Watch approved matches, simulate fills, and record positions and results. |
| 7 | Answer questions | Agent + tracking tools | The agent queries stored results to explain opportunities and portfolio performance. |

**Where the numbers come from:** prices, quantities, and fees come from the platform APIs and fee schedules; all arithmetic (interval checks, profit, sizing) is done in code, not by the LLM.

## 4. Prior Work and What's New

**Builds on:** [cross-platform-pm-arbitrage](https://github.com/AgenticsFintekColumbia/cross-platform-pm-arbitrage)

**What we will do differently:**

- Add an **LLM-as-a-judge** to challenge each proposed match.
- Build an **error-driven loop**: feed wrong cases back into the evaluation set and evaluate against it.
  - Build a **human-labeled set of markets** to evaluate performance.

## 5. Workplan and Ownership

*(Lead for each workstream in parentheses.)*

| # | Workstream | Lead | Description |
|---|------------|------|-------------|
| 1 | Collect and standardize data | Colin | Pull Kalshi and Polymarket markets, rules, fees, and order books into a common schema; snapshot books regularly. |
| 2 | Find candidates | Stefan | Use platform filters and embeddings of titles and rules to identify potential matches. |
| 3 | Explain matches | Leo | Have an LLM propose equivalent or complementary outcomes, including baskets, and explain the relationship. |
| 4 | Verify matches | Leo | Check numeric claims deterministically, including boundaries, units, and dates. Use an independent LLM judge for non-numeric claims; send disagreements to human review. |
| 5 | Review rules | Riddhi | Compare resolution sources, deadlines, time zones, and exceptional outcomes. Flag differences for human approval before trading. |
| 6 | Evaluate and improve | Riddhi | Test against human-labeled examples. Add reviewed failures to the eval set and refine the agent. Keep a separate held-out test set for evaluation. |
| 7 | Monitor arbitrage | Stefan | Continuously check executable ask prices, fees, depth, and slippage to identify profitable opportunities at a given size. |
| 8 | Execute trades | Kaspar | Trade only verified, rules-approved opportunities within position limits; handle partial fills and failed legs. |
| 9 | Track trades | Kaspar | Monitor each leg, fills, prices, realized and unrealized PnL, exposure, and time to resolution. |
| 10 | Optimize positions *(optional)* | Stefan | Size and adjust positions based on arbitrage spread, time to expiration, available capital, liquidity, and slippage. |

**By owner:**

- **Colin:** data collection and standardization (1)
- **Stefan:** candidate finding (2), arbitrage monitoring (7), position optimization (10, optional)
- **Leo:** match explanation (3), match verification (4)
- **Riddhi:** rules review (5), evaluation and improvement (6)
- **Kaspar:** trade execution (8), trade tracking (9)

## 6. What to Cut First

1. **Optimize positions** (workstream 10).
2. **Execute and track trades** (workstreams 8–9): fall back to identifying opportunities and paper trading with fake money only.
