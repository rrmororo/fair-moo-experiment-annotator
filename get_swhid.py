#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
get_swhid.py -- obtain the REAL Software Heritage SWHID of a repository.

Usage:
    py get_swhid.py https://github.com/bmatb/ricardo-dissertacao-experiments-dados

It queries the Software Heritage GraphQL API for the latest archived snapshot
of the given repository origin and prints the snapshot SWHID. If the repo is
not archived yet, it prints the 'Save Code Now' URL -- open it once, wait for
the visit to finish, then run this again.

Paste the returned SWHID into the app's "Software Heritage ID (SWHID)" field
(schema key: code_swhid). Nothing is invented: if the archive has no snapshot,
no SWHID is produced.
"""
import sys
import swh


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    repo = sys.argv[1].strip()
    print(f"Querying Software Heritage for: {repo}\n")
    res = swh.snapshot_swhid_for_origin(repo)

    if res["swhid"]:
        print("SWHID (latest snapshot):")
        print("   ", res["swhid"])
        print("\nContextual permalink (store this in the RO / cite this):")
        print("   ", swh.browse_url(res["swhid"]))
        print("\nContext-qualified SWHID (recommended for citation):")
        print(f"    {res['swhid']};origin={repo}")
        if res["visit_date"]:
            print(f"\nLast archived visit: {res['visit_date']}")
        print("\n-> Paste the SWHID into the app field 'Software Heritage ID "
              "(SWHID)'.")
    else:
        print("Not archived yet (or no snapshot).")
        if res["error"]:
            print("Reason:", res["error"])
        print("\nRequest archiving here (open once, then re-run this script):")
        print("   ", swh.save_url(repo))


if __name__ == "__main__":
    main()
