import sys

from matcheyes import __version__


def main() -> int:
    sys.stdout.write(f"MatchEyes {__version__} - See beyond the score.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
