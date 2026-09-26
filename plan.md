# Bybit broker integration plan

## Current state

- Bybit platform registration, authentication scaffold, category-aware symbol master, public market data, account read APIs, dashboard/holdings display, and streaming implementation are present.
- `BYBIT_TESTNET=true` routes Bybit REST, server-time checks, master download, and public/private WebSockets to testnet. `BYBIT_BASE_URL` remains the mainnet/regional REST override when testnet is false.
- Spot, Linear, Inverse, and Options are represented in the symbol master and market-data paths. Spot `minOrderQty` and `maxOrderQty` are now retained for validation.
- Order placement, modification, cancellation, cancel-all, smart-order position lookup, and close-position paths have category-aware local implementations. Order requests validate instrument metadata, quantity steps, price ticks, and supported product/order combinations.
- Bybit realtime order scans retry category-specific required filters (`settleCoin`/`baseCoin`) when category-only queries return retCode 10001. A successful filtered response with no open orders is treated as a valid empty result instead of re-raising the rejected initial query.
- Bybit market-data date parsing now uses Kazakhstan timezone (`Asia/Almaty`) instead of `Asia/Kolkata`.
- Bybit REST calls now use pybit's typed V5 methods for authentication, public market data, instrument master, account data, and order lifecycle requests. Its request signing and retry handling replace the plugin's custom REST signature code; the shared pybit HTTP session is closed on reconfiguration and process shutdown. Under eventlet, blocking SDK calls run via its thread pool and are serialized to protect the shared requests session.
- The pybit REST client uses a 10-second receive window and retries Bybit timestamp-window rejections. The observed ~5.9-second server offset should fit this window, but live account verification is still required.
- The latest targeted Bybit suite passed: 68 tests. The private/public WebSocket implementation remains separate and has not been migrated to pybit or live-validated.
- This is not live-validated. The latest reported REST errors were retCode 10002 (about 5.9 seconds of clock skew) and retCode 10004 (signature mismatch); no live account-data requests, real orders, or authenticated WebSocket checks were run after the pybit migration.
- Do not treat a successful OpenAlgo login or analyzer-mode order as proof of Bybit authentication or order execution. Analyzer mode does not reach the broker API.

## Immediate user-side check: choose the right credential for the test

Read-only credentials and testnet credentials serve different checks:

1. To isolate authentication and account-read access without order risk, use a fresh mainnet key with read-only permissions. It verifies signed wallet/order/position reads only; it cannot test order placement.
2. To test the order lifecycle, create a separate key in Bybit Testnet and set `BYBIT_TESTNET=true`. Testnet trading permissions are appropriate there because the account is simulated and separate from mainnet.
3. Do not reuse or paste previously shared credentials. Confirm the key belongs to the same environment selected in configuration.
4. If IP restrictions are enabled, register the server's current public egress IP, not the workstation's private/LAN address.
5. Confirm the server clock is synchronized. Retry once after checking configuration; repeated 401s should be diagnosed from sanitized status, response metadata, selected environment, and clock-offset logs.
6. Never send API key, secret, signature, auth headers, or signed request data in screenshots or chat. Report only sanitized outcomes.

Pass condition: authentication succeeds against the selected environment. A mainnet read-only key gates only mainnet account reads; testnet order checks must authenticate with a Testnet key.

### Switching environments

- Set `BYBIT_TESTNET=true` for testnet, or `false` for mainnet.
- When false, `BYBIT_BASE_URL` may select the explicitly intended mainnet/regional REST host. When true, the testnet REST host is selected regardless of `BYBIT_BASE_URL`.
- Restart OpenAlgo and its WebSocket proxy after changing `.env`, reconnect Bybit with credentials from that environment, and refresh the Bybit master. The local broker session and symbol master represent the selected environment; do not expect mainnet and testnet sessions to run side-by-side in one instance.
- WebSocket traffic follows the same switch. Testnet API keys and mainnet API keys are not interchangeable.

## Remaining phases

### Phase 1: authenticated read-only account verification

- Verify wallet/funds and dashboard values against the Bybit account UI, including USD-equivalent equity and per-coin balances.
- Verify orderbook, tradebook, and positions for each category the account actually uses. Check pagination, timestamps, duplicate handling, empty responses, and error messages.
- Confirm Unified Account mode assumptions. Record unsupported account modes or endpoint permission requirements explicitly.
- Keep API key trading permission disabled.

### Phase 2: master and public market-data matrix

- Refresh the master and verify Spot, Linear, Inverse, and Options symbols survive together; check representative symbols, precision, minimums, expiry, strike, and category.
- Check quotes, multi-quotes, depth, intervals, and history for representative instruments from every supported category.
- Compare returned values and timestamps with Bybit's UI/public API. Options history and any unsupported interval/category combinations must fail clearly rather than return fabricated data.

### Phase 3: order-contract review and testnet/demo validation

- First finish offline contract tests for create/amend/cancel/cancel-all, order status, smart orders, and close-all, including one-way/hedge position modes, conditional orders, and partial failures.
- Use Bybit testnet/demo credentials and a small, explicitly approved test order; verify acknowledgement in the Bybit order book, then cancel it. An HTTP success alone is not proof of a fill or final order state.
- Verify Spot and all derivative categories independently. Test minimum/step/tick boundaries and broker rejections.
- Do not use a mainnet key with trading permission until testnet/demo checks pass and the operator explicitly approves a live order.
- Current close-all support is intentionally limited: it closes Linear/Inverse derivative positions and refuses the whole operation before sending if unsupported Options positions are present. Spot wallet balances are not positions and are not sold.

### Phase 4: streaming and order-update verification

- After read-only authentication is confirmed, verify public market-data subscriptions and private order/execution/position updates.
- Check reconnect, unsubscribe, duplicate subscription, and disconnect behavior. Confirm ticks reach the UI through the proxy/ZMQ path and order updates reach their consumers.

### Phase 5: hardening and release readiness

- Run targeted Bybit tests, relevant cross-broker regression tests, frontend build/lint for touched UI, migration checks, and `fd-audit`.
- Verify user-facing failures, rate-limit handling, and that logs do not expose credentials or signed data.
- Document supported categories, account modes, limitations, and exact live-test coverage. Add a changelog entry before publishing.

## Gate sequence

`fresh mainnet read-only key -> authenticated mainnet account reads -> public category data matrix -> offline order contract -> separate testnet key + BYBIT_TESTNET=true -> testnet order lifecycle and streaming -> explicitly approved mainnet smoke test -> release hardening`

Never skip a gate when the previous one fails. No real order should be sent without explicit opt-in.
