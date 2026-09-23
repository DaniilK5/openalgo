# Bybit broker integration plan

## Current state
- Research and auth scaffold are completed.
- Bybit is registered in platform config, login flow and proxy wiring.
- The project has a working scaffold, but not a live-validated Bybit integration yet.
- Operational note: the app can log in to OpenAlgo normally and then redirect to /broker when there is no stored broker session. This is expected before the user connects a broker. The real blocker is a missing or inactive broker config for the selected broker, such as Bybit not being present in VALID_BROKERS / REDIRECT_URL.

## Recommended execution path
The staged Bybit integration has reached the final verification pass: contract validation, master-symbol build, REST market-data, signed order/account flow, and streaming registration have all been completed. The remaining work is live hardening with a real account and real API keys, not a broad rewrite.

Why:
- The symbol contract, public market-data contract, signed order/account contract and streaming adapter have all been validated in code and through package-level smoke tests.
- The only stage still dependent on real credentials is the authenticated live hardening check against the real Bybit account and live WebSocket feed.
- This keeps the integration incremental and reduces the risk of shipping a broker integration built on assumptions rather than confirmed responses.

## Planned phases
1. Stage 1: research and contract validation
   - Done: validated the live `/v5/market/instruments-info` endpoint and the auth-signature flow.

2. Stage 2: platform wiring
   - Done: registration, UI, env, login branch, proxy adapter.

3. Stage 3: master contract and symbol mapping
   - Done: Bybit linear perpetual/futures are downloaded and written to `symtoken`.

4. Stage 4: market data REST
   - Done: live quotes, depth and history are implemented and validated against public V5 endpoints.

5. Stage 5: orders and account data
   - Done: signed order/account mapping, margins and holdings/position normalization implemented.

6. Stage 6: WebSocket streaming
   - Done: public market-data + private account adapter and proxy registration implemented and smoke-tested.

7. Stage 7: live hardening and verification
   - Done for code-level validation: compileall, package import, adapter init, subscription/unsubscription smoke tests passed.
   - Remaining only with real API credentials: authenticated wallet/order/position tests and live subscription validation.

## Decision
The staged implementation is complete enough for the code path and proxy integration; the final gate is live broker validation with actual Bybit credentials, which should be run only in a real account environment.
