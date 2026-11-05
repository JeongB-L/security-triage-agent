# Dependency Vulnerability Remediation Report

This report summarizes the vulnerabilities found in the current dependencies and the recommended fixes.

## 1. axios (version 0.21.0)
- **Worst CVE:** CVE-2025-62718
- **Severity:** Critical (CVSS 9.9)
- **Description:** Hostname normalization bypass in NO_PROXY leading to SSRF and proxy bypass.
- **CISA KEV:** Not listed
- **EPSS:** 0.01186 (67.9 percentile)
- **Fix version:** Upgrade to 1.15.0 or 0.31.0

## 2. lodash (version 4.17.15)
- **Worst CVE:** CVE-2026-4800
- **Severity:** High (CVSS 8.1)
- **Description:** Code injection via _.template imports key names.
- **CISA KEV:** Not listed
- **EPSS:** 0.02808 (86.5 percentile)
- **Fix version:** Upgrade to 4.18.1

## 3. jsonwebtoken (version 8.5.1)
- **Worst CVE:** CVE-2022-23539
- **Severity:** Medium (CVSS 5.9)
- **Description:** Misconfiguration allowing legacy insecure key types for signature verification.
- **CISA KEV:** Not listed
- **EPSS:** 0.00524 (44.6 percentile)
- **Fix version:** Upgrade to 9.0.0

## 4. minimist (version 1.2.0)
- **Worst CVE:** CVE-2021-44906
- **Severity:** High (CVSS 7.8)
- **Description:** Prototype pollution vulnerability.
- **CISA KEV:** Not listed
- **EPSS:** Not checked
- **Fix version:** Upgrade to 1.2.6

---

Please prioritize upgrading the packages in the order listed above to mitigate the most critical vulnerabilities first.