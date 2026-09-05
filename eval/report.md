# Reclaim Evaluation Report (Held-Out Split)

This report compares the performance of Strategy A (Naive), Strategy B (Rules-Only), and Strategy C (LLM + Policy Engine) on the final, untouched `held_out` split.

## 1. Recovery Metrics

| Metric | Strategy A | Strategy B | Strategy C |
|---|---|---|---|
| Total events | 15 | 15 | 15 |
| Recovered count | 3 | 7 | 3 |
| Recovery rate (%) | 20.00% | 46.67% | 20.00% |
| Total INR recovered | Rs.9,403.41 | Rs.33,010.68 | Rs.22,346.01 |
| Total attempts | 15 | 21 | 9 |
| INR per intervention | Rs.626.89 | Rs.1,571.94 | Rs.2,482.89 |

### Strategy C Source Breakdown
- **LLM Decisions:** 15 (100.0%)
- **Fallback Heuristic:** 0

## 2. Exception List (Unrecovered Payments)

The following payments were fundamentally unrecoverable or exhausted their retry attempts.

### Strategy A Exceptions (12)

| Payment ID | Amount | Root Cause Bucket | Reason Category | Error Code | Raw Error |
|---|---|---|---|---|---|
| `pay_synth_0034_64d756` | Rs.20922.45 | `soft_decline` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_PAYMENT_DECLINED_BY_BANK` | Payment was declined by the issuing bank... |
| `pay_synth_0087_18e64e` | Rs.974.75 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `GATEWAY_ERROR_INSUFFICIENT_FUNDS` | Declined: not enough balance in account.... |
| `pay_synth_0010_275952` | Rs.828.05 | `soft_decline` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_PAYMENT_DECLINED_BY_BANK` | Payment was declined by the issuing bank... |
| `pay_synth_0135_62c8b8` | Rs.2582.67 | `risky` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_POSSIBLE_FRAUD` | High risk score detected: rapid successi... |
| `pay_synth_0139_8958ab` | Rs.4038.75 | `risky` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_POSSIBLE_FRAUD` | High risk score detected: rapid successi... |
| `pay_synth_0130_423a63` | Rs.600.46 | `risky` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_RISK_CHECK_FAILED` | Transaction flagged by Razorpay Thirdwat... |
| `pay_synth_0026_bdbcc5` | Rs.13075.51 | `soft_decline` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_PAYMENT_AUTH_FAILED` | Customer 3D-Secure authentication failed... |
| `pay_synth_0076_8f78db` | Rs.3300.22 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_PAYMENT_ACCOUNT_INSUFFICIENT_BALANCE` | Payment failed due to insufficient funds... |
| `pay_synth_0040_b83436` | Rs.15769.04 | `soft_decline` | Ground-Truth Unrecoverable (Attempted, Failed) | `GATEWAY_ERROR_DO_NOT_HONOR` | Issuer returned do not honor status code... |
| `pay_synth_0058_ce6fc9` | Rs.4400.32 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_INSUFFICIENT_FUNDS` | Customer account does not have sufficien... |
| `pay_synth_0050_737dba` | Rs.882.02 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `GATEWAY_ERROR_INSUFFICIENT_FUNDS` | Declined: not enough balance in account.... |
| `pay_synth_0125_c26c72` | Rs.3269.81 | `hard_decline` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_CARD_EXPIRED` | The card expiry date is in the past.... |

### Strategy B Exceptions (8)

| Payment ID | Amount | Root Cause Bucket | Reason Category | Error Code | Raw Error |
|---|---|---|---|---|---|
| `pay_synth_0135_62c8b8` | Rs.2582.67 | `risky` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_POSSIBLE_FRAUD` | High risk score detected: rapid successi... |
| `pay_synth_0139_8958ab` | Rs.4038.75 | `risky` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_POSSIBLE_FRAUD` | High risk score detected: rapid successi... |
| `pay_synth_0130_423a63` | Rs.600.46 | `risky` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_RISK_CHECK_FAILED` | Transaction flagged by Razorpay Thirdwat... |
| `pay_synth_0026_bdbcc5` | Rs.13075.51 | `soft_decline` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_PAYMENT_AUTH_FAILED` | Customer 3D-Secure authentication failed... |
| `pay_synth_0076_8f78db` | Rs.3300.22 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_PAYMENT_ACCOUNT_INSUFFICIENT_BALANCE` | Payment failed due to insufficient funds... |
| `pay_synth_0040_b83436` | Rs.15769.04 | `soft_decline` | Ground-Truth Unrecoverable (Attempted, Failed) | `GATEWAY_ERROR_DO_NOT_HONOR` | Issuer returned do not honor status code... |
| `pay_synth_0058_ce6fc9` | Rs.4400.32 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_INSUFFICIENT_FUNDS` | Customer account does not have sufficien... |
| `pay_synth_0125_c26c72` | Rs.3269.81 | `hard_decline` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_CARD_EXPIRED` | The card expiry date is in the past.... |

### Strategy C Exceptions (12)

| Payment ID | Amount | Root Cause Bucket | Reason Category | Error Code | Raw Error |
|---|---|---|---|---|---|
| `pay_synth_0097_6e39c0` | Rs.4352.51 | `network_error` | Ground-Truth Unrecoverable (Attempted, Failed) | `GATEWAY_ERROR_PAYMENT_TIMED_OUT` | Connection to bank gateway timed out aft... |
| `pay_synth_0087_18e64e` | Rs.974.75 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `GATEWAY_ERROR_INSUFFICIENT_FUNDS` | Declined: not enough balance in account.... |
| `pay_synth_0135_62c8b8` | Rs.2582.67 | `risky` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_POSSIBLE_FRAUD` | High risk score detected: rapid successi... |
| `pay_synth_0139_8958ab` | Rs.4038.75 | `risky` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_POSSIBLE_FRAUD` | High risk score detected: rapid successi... |
| `pay_synth_0130_423a63` | Rs.600.46 | `risky` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_RISK_CHECK_FAILED` | Transaction flagged by Razorpay Thirdwat... |
| `pay_synth_0026_bdbcc5` | Rs.13075.51 | `soft_decline` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_PAYMENT_AUTH_FAILED` | Customer 3D-Secure authentication failed... |
| `pay_synth_0076_8f78db` | Rs.3300.22 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_PAYMENT_ACCOUNT_INSUFFICIENT_BALANCE` | Payment failed due to insufficient funds... |
| `pay_synth_0040_b83436` | Rs.15769.04 | `soft_decline` | Ground-Truth Unrecoverable (Attempted, Failed) | `GATEWAY_ERROR_DO_NOT_HONOR` | Issuer returned do not honor status code... |
| `pay_synth_0036_42a715` | Rs.4455.39 | `soft_decline` | Ground-Truth Unrecoverable (Attempted, Failed) | `GATEWAY_ERROR_DO_NOT_HONOR` | Issuer returned do not honor status code... |
| `pay_synth_0058_ce6fc9` | Rs.4400.32 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `BAD_REQUEST_INSUFFICIENT_FUNDS` | Customer account does not have sufficien... |
| `pay_synth_0050_737dba` | Rs.882.02 | `insufficient_funds` | Ground-Truth Unrecoverable (Attempted, Failed) | `GATEWAY_ERROR_INSUFFICIENT_FUNDS` | Declined: not enough balance in account.... |
| `pay_synth_0125_c26c72` | Rs.3269.81 | `hard_decline` | Policy-Blocked (Never Attempted) | `BAD_REQUEST_PAYMENT_CARD_EXPIRED` | The card expiry date is in the past.... |

## 3. Disagreement Analysis: Strategy C (LLM) vs Strategy B (Rules)

- **Total events compared:** 15
- **Agreements:** 14 (93.3%)
- **Disagreements:** 1 (6.7%)

| Event ID | Bucket | B Action | C Action | C Reasoning |
|---|---|---|---|---|
| 15 | `hard_decline` | `stop` | `switch_method` | The decline is classified as a hard decline with error BAD_REQUEST_PAYMENT_CARD_... |
