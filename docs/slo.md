# Service Level Objectives

## SLO Definitions

| Service | Objective | Window |
|---------|-----------|--------|
| API availability | 99% of requests to `/v1/*` return a non-5xx response | 30-day rolling |
| ETL reliability | >= 23 of 24 hourly runs succeed per day per active region | 24 h |

- API availability excludes `429 Too Many Requests` responses from both
  numerator and denominator.
- ETL success is defined as a Step Functions execution reaching the
  `Succeeded` terminal state.

## Measurement Methodology

**API** - `AWS/ApiGateway` namespace: 5XXError count vs total request
count over a 30-day rolling window.

**ETL** - Step Functions namespace: `ExecutionsSucceeded` vs
`ExecutionsStarted` per 24-hour window, partitioned by region.

## Alerting Thresholds

| Metric | Condition | Action |
|--------|-----------|--------|
| API 5xx rate | > 1% over 5 minutes | Triggers CloudWatch alarm |
| ETL execution | Any ABORTED execution in 24 h | Triggers CloudWatch alarm |
| API p95 latency | > 500 ms | Triggers CloudWatch alarm |

## Error Budget

- **API**: 1% budget = ~7.3 hours/month of allowed downtime.
- **ETL**: 1 missed run per day per region is within budget.
- **When budget is exhausted**: freeze feature deployments; focus
  exclusively on reliability until budget recovers.
