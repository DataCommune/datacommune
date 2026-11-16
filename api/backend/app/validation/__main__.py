"""Allow running as: python -m app.validation [datasets...]"""

from app.validation.run import main

if __name__ == "__main__":
    raise SystemExit(main())
