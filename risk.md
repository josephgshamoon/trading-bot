# Risk Management Framework

## Account Risk Limits
| Parameter | Value | Action |
|-----------|-------|--------|
| Max Daily Loss | 20% | Kill switch activates |
| Max Drawdown | 20% | Stop all trading |
| Max Trades/Day | 2 | Cease trading |
| Position Size | $1 | Fixed (Phase 1) |
| Leverage | 0x | No leverage |

## Safety Layer
- [ ] Kill switch (manual)
- [ ] Daily loss cap
- [ ] Max position size
- [ ] Max trades/day limit
- [ ] Circuit breaker
- [ ] Telegram notification on all events

## Trade Approval Workflow
1. Signal detected
2. Telegram alert sent
3. Manual approve required
4. Trade execution (by user or bot)
5. Confirmation logged

## Logging Requirements
- Every signal logged
- Every decision logged
- Every trade logged
- Errors logged with stack trace
- Daily summary generated

## Emergency Contacts
- Primary: (configure in .env)
