# Phase 7 Adversarial Testbed Report

Generated: 2026-08-03 12:15:30

## Headline rates

- **Prevention rate** = prevented / total_scenarios = 11/18 = 61%
- **Detection rate** = detected / total_scenarios = 6/18 = 33%

Prevention and detection are measured independently (a scenario can be either, both, or neither) -- see PROGRESS.md's Phase 7 entry for why a single pass/fail column would understate the picture.

## Results

| Category | Scenario | Expected | Observed | Prevented? | Detected? |
|---|---|---|---|---|---|
| 1 | unauthorized internal join -- no client cert | PREVENTED | PREVENTED | yes | no |
| 1 | unauthorized internal join -- rogue self-signed CA cert | PREVENTED | PREVENTED | yes | no |
| 2 | gossip 'sender' field accepted with a fabricated identity (node-2's real cert used) | UNDEFENDED | UNDEFENDED (accepted identically) | no | no |
| 2 | hint 'target' field accepted for an arbitrary/fake node id (node-2's real cert used) | UNDEFENDED | UNDEFENDED (accepted) | no | no |
| 3 | compromised relay suppresses a healthy node's perceived liveness (topology: node-1 reachable to node-3 only via node-2) | UNDEFENDED | inconclusive -- baseline write latency was already fast (1.17s), so the latency-based observable can't distinguish this run's outcome. Docker's network-level segmentation (separate bridge networks) causes near-instant connection/DNS failures rather than a hung TCP timeout, so a REACTIVE failed attempt to unreachable node-3 is already about as fast as a PROACTIVE gossip-driven skip would be -- the two paths are latency-indistinguishable under this specific segmentation technique, independent of whether the forged relay actually took effect. | no | no |
| 4 | replay a captured old write after a newer one has landed | PREVENTED | PREVENTED (replay dropped, v2 retained) | yes | no |
| 5 | fabricated clock claimed for a node the caller doesn't represent hijacks the key cluster-wide | UNDEFENDED (immediate hijack) | UNDEFENDED (hijack succeeded) | no | no |
| 5 | self-heal boundary -- impersonated node's next real coordinated write supersedes the poison | the poison is NOT eternal | self-healed as expected | yes | no |
| 6 | delete all replicas directly via the internal API, with no legitimate client-facing delete ever issued | UNDEFENDED | UNDEFENDED (key was deleted with no authorization) | no | no |
| 7 | decrypt raw on-disk ciphertext with the wrong key | PREVENTED | PREVENTED (wrong key raises) | yes | no |
| 8 | flip a byte in stored ciphertext, then read that specific replica directly (its own internal endpoint) | PREVENTED | PREVENTED (tampered read rejected/failed) | yes | no |
| 9 | auth bypass attempt -- no Authorization header | 401 | 401 | yes | yes |
| 9 | auth bypass attempt -- malformed Authorization header | 401 | 401 | yes | yes |
| 9 | auth bypass attempt -- unknown token | 401 | 401 | yes | yes |
| 9 | auth bypass attempt -- read-only token attempting write | 403 | 403 | yes | yes |
| 9 | auth bypass attempt -- fully valid token | 200 | 200 | yes | yes |
| 10a | internal-port attacks (categories 2/3/5/6) are captured in an audit trail | FIXED this phase (audit middleware attached to the internal app) | internal audit log has 60 entries | no | yes |
| 10b | a compromised node truncates/edits its own local audit log with no integrity check | UNDEFENDED | UNDEFENDED (truncation succeeded, no error/alarm) | no | no |

## Notes
- **3 (compromised relay suppresses a healthy node's perceived liveness (topology: node-1 reachable to node-3 only via node-2))**: The chosen live observable (write-latency delta) doesn't cleanly distinguish proactive-skip from reactive-fail-fast when Docker network segmentation causes near-instant failures rather than slow timeouts. This does NOT mean the attack is defended -- it means this specific live-reproduction technique is inconclusive by construction. The underlying mechanism is proven deterministically, independent of Docker/network timing, in tests/test_gossip.py::test_adversarial_stale_relay_makes_a_healthy_node_appear_down.
- **10b (a compromised node truncates/edits its own local audit log with no integrity check)**: Tamper-evident logging (hash chaining or signing) is Phase 9-adjacent structured-logging territory, not attempted here.
