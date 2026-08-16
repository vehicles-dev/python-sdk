# Security policy

## Supported versions

Security fixes are released for the latest published version of `vehicles-dev`.

## Reporting a vulnerability

Please do not open a public GitHub issue for a suspected vulnerability. Email
<support@vehicles.dev> with `SECURITY` in the subject and include:

- the affected SDK version;
- a minimal reproduction or proof of concept;
- the impact you observed; and
- any suggested mitigation.

Do not include a live Vehicles.dev API key. If a credential may have been exposed, revoke it in the
Vehicles.dev dashboard immediately and create a replacement.

We will acknowledge a report as soon as practical and coordinate remediation and disclosure with
the reporter.

## Server-side credential boundary

This SDK is for trusted server-side runtimes. Never ship a Vehicles.dev API key in browser code,
mobile application assets, public logs, source control, or a client-visible environment variable.

