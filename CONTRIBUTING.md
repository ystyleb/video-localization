# Contributing

Thanks for your interest in contributing to video-localization!

## Development Setup

```bash
# Clone the repo
git clone https://github.com/ystyleb/video-localization.git
cd video-localization

# Install dependencies (requires uv)
uv sync

# Install dev dependencies
uv sync --extra dev

# Install system dependencies
brew install ffmpeg
```

## Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting with a line length of 100 characters.

```bash
# Check
uv run ruff check src/ scripts/

# Auto-fix
uv run ruff check --fix src/ scripts/
```

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Run `uv run ruff check src/ scripts/` to ensure code style compliance
4. Run `uv run python -m scripts.doctor` to verify environment setup
5. Submit a pull request with a clear description of what you changed and why
