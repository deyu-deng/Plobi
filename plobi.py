#!/usr/bin/env python3
"""
Plobi Agent CLI launcher.

This wrapper should behave like the installed `plobi` command, including
subcommands such as `gateway`, `cron`, and `doctor`.
"""

if __name__ == "__main__":
    from plobi_cli.main import main
    main()
