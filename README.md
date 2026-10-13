# Thiago Crawl Worker

Generic public crawl runner for the Thiago Crawl control plane.

This repository contains only generic crawler code, safe synthetic fixtures, tests, and a hardened GitHub Actions workflow. It intentionally contains no crawl requests, corpus data, credentials, private control-plane code, or research history.

Production jobs are designed to receive only an opaque `job_id`; private request details and results stay behind the authenticated control plane.
