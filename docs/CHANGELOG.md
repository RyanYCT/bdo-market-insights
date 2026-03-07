# Changelog

All notable changes to the BDO Market Insights project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CI/CD pipeline with GitHub Actions
- Blue-green deployment for Lambda functions
- Automated testing (unit, property-based, integration)
- Code quality checks (Black, Flake8, MyPy, Bandit)
- Deployment scripts for manual deployment
- Rollback scripts for emergency recovery
- Comprehensive deployment documentation

### Changed
- N/A

### Deprecated
- N/A

### Removed
- N/A

### Fixed
- N/A

### Security
- Added Bandit security scanning to CI/CD pipeline

## [1.0.0] - YYYY-MM-DD

### Added
- Initial release of BDO Market Insights rewrite
- Lambda Layer with shared utilities
- Pydantic validation schemas
- Secrets Manager integration
- Structured JSON logging with correlation IDs
- Database connection pooling
- Error handling with retry logic and circuit breaker
- RESTful Query API with GET endpoints
- API authentication with API keys
- Data retention automation
- CloudWatch monitoring and X-Ray tracing
- OpenAPI documentation
- Comprehensive test suite

### Changed
- Migrated from POST to GET for query endpoints
- Improved error handling and logging
- Enhanced security with Secrets Manager

### Security
- Implemented API key authentication
- Added SQL injection prevention
- Secure credential management with Secrets Manager

---

## Template for New Releases

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- New features

### Changed
- Changes to existing functionality

### Deprecated
- Features that will be removed in future releases

### Removed
- Features that have been removed

### Fixed
- Bug fixes

### Security
- Security improvements and fixes
```

## Deployment Notes

### How to Update This File

1. **During Development**: Add changes to the `[Unreleased]` section
2. **Before Release**: Move unreleased changes to a new version section
3. **After Deployment**: Update version number and date

### Version Numbering

- **Major (X.0.0)**: Breaking changes, major new features
- **Minor (0.X.0)**: New features, backward compatible
- **Patch (0.0.X)**: Bug fixes, minor improvements

### Example Entry

```markdown
## [1.2.3] - 2024-01-15

### Added
- New retry logic for External API calls (#123)
- CloudWatch dashboard for monitoring (#124)

### Changed
- Increased Lambda timeout from 30s to 60s (#125)
- Updated Python runtime to 3.14 (#126)

### Fixed
- Fixed connection pool exhaustion issue (#127)
- Corrected date range validation in query API (#128)

### Security
- Updated dependencies to patch CVE-2024-12345 (#129)
```

## Deployment History

Track major deployments here:

| Version | Date | Environment | Deployed By | Notes |
|---------|------|-------------|-------------|-------|
| 1.0.0 | YYYY-MM-DD | Production | GitHub Actions | Initial release |

---

For detailed commit history, see: https://github.com/your-org/bdo-market-insights/commits/main
