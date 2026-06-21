"""CLI entry point.  Thin wrapper: parsing/dispatch lives in app.do_command_line.

Usage:
  clipshot [--daemon]        start the tray daemon (run at login)
  clipshot --region          capture a region   (default if no args)
  clipshot --fullscreen      capture whole screen
  clipshot --window          capture a window
  clipshot --ocr             extract text from a region
  clipshot --previous        repeat last region
  clipshot --timer=5         region capture after 5s
  clipshot --settings        open settings
  clipshot --history         open capture history
"""
import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
