# bdo-market-insights

Serverless market-data platform for Black Desert Online on AWS Lambda.

## Architecture

See [docs/architecture.md](docs/architecture.md) for system overview.

## Quick Start

### Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- AWS credentials configured

### Setup

```bash
pyenv local 3.12
uv sync
```

## Development

```bash
make lint        # ruff check + format check
make format      # auto-format with ruff
make typecheck   # mypy strict mode
make test        # pytest
```

## Deployment

```bash
make deploy-dev   # deploy to dev environment
make deploy-prod  # deploy to prod environment
```

Or push a `v*` tag to trigger CI-driven production deploy.

## Project Structure

See [.kiro/steering/structure.md](.kiro/steering/structure.md) for the full repository layout.

## Documentation

- [Architecture](docs/architecture.md)
- [Runbook](docs/runbook.md)
- [SLO Definitions](docs/slo.md)
- [ADRs](docs/adr/)

## License

MIT - see [LICENSE](LICENSE).
