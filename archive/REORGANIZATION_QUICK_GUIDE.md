# Repository Reorganization Quick Guide

## ✅ REORGANIZATION COMPLETE

**Status:** Completed on March 6, 2026  
**Result:** Successfully reduced root directory from 47 to 18 items (62% reduction)

See [REORGANIZATION_COMPLETE.md](REORGANIZATION_COMPLETE.md) for full details.

---

## Original Quick Guide (For Reference)

## TL;DR

**Problem:** 47 items in root directory (too cluttered)
**Solution:** Reorganize into logical structure
**Result:** 18 items in root (62% reduction)

## Quick Commands

### Option A: Full Reorganization (Recommended)

```bash
# 1. Create new structure
mkdir -p docs/deployment docs/guides docs/architecture src

# 2. Move documentation
mv QUICK_START.md docs/deployment/
mv CREDENTIALS_SETUP_GUIDE.md docs/deployment/
mv DEPLOYMENT.md docs/deployment/
mv DEPLOYMENT_SECURITY_SUMMARY.md docs/deployment/
mv STAGING_DEPLOYMENT_SUMMARY.md docs/deployment/
mv DOCUMENTATION_MAP.md docs/guides/
mv DOCUMENTATION_IMPROVEMENTS_SUMMARY.md docs/guides/
mv CHANGELOG.md docs/

# 3. Move Lambda functions
mv analyzeData src/
mv cleanData src/
mv fetchData src/
mv queryData src/
mv retainData src/
mv retrieveIdList src/
mv storeData src/
mv testDatabaseConnection src/

# 4. Move architecture images (optional)
mv img/architecture/* docs/architecture/ 2>/dev/null || true

# 5. Update references (see below)
```

### Option B: Documentation Only (Quick & Safe)

```bash
# 1. Create docs directory
mkdir -p docs

# 2. Move all markdown files (except README)
mv QUICK_START.md docs/
mv CREDENTIALS_SETUP_GUIDE.md docs/
mv DEPLOYMENT.md docs/
mv DEPLOYMENT_SECURITY_SUMMARY.md docs/
mv STAGING_DEPLOYMENT_SUMMARY.md docs/
mv DOCUMENTATION_MAP.md docs/
mv DOCUMENTATION_IMPROVEMENTS_SUMMARY.md docs/
mv CHANGELOG.md docs/

# 3. Update references (see below)
```

## Files to Update After Reorganization

### 1. README.md

**Find and replace:**
- `QUICK_START.md` → `docs/deployment/QUICK_START.md` (or `docs/QUICK_START.md`)
- `CREDENTIALS_SETUP_GUIDE.md` → `docs/deployment/CREDENTIALS_SETUP_GUIDE.md`
- `DEPLOYMENT.md` → `docs/deployment/DEPLOYMENT.md`
- `DEPLOYMENT_SECURITY_SUMMARY.md` → `docs/deployment/DEPLOYMENT_SECURITY_SUMMARY.md`
- `STAGING_DEPLOYMENT_SUMMARY.md` → `docs/deployment/STAGING_DEPLOYMENT_SUMMARY.md`
- `DOCUMENTATION_MAP.md` → `docs/guides/DOCUMENTATION_MAP.md`
- `CHANGELOG.md` → `docs/CHANGELOG.md`

### 2. Deployment Scripts (if moving Lambda functions)

**Files to update:**
- `scripts/deploy-all.sh`
- `scripts/deploy-function.sh`
- `scripts/deploy-staging.sh`
- `scripts/deploy-staging.bat`

**Find and replace:**
- `analyzeData/` → `src/analyzeData/`
- `cleanData/` → `src/cleanData/`
- `fetchData/` → `src/fetchData/`
- `queryData/` → `src/queryData/`
- `retainData/` → `src/retainData/`
- `retrieveIdList/` → `src/retrieveIdList/`
- `storeData/` → `src/storeData/`

### 3. GitHub Actions (if moving Lambda functions)

**File:** `.github/workflows/deploy.yml`

**Update paths:**
- Lambda function directories
- Deployment script paths

### 4. Makefile (if moving Lambda functions)

**Update:**
- Lambda function paths
- Test paths if needed

## Testing Checklist

After reorganization:

- [ ] All links in README.md work
- [ ] All documentation cross-references work
- [ ] Deployment scripts run successfully
- [ ] GitHub Actions workflow runs
- [ ] Tests still pass
- [ ] Lambda functions can be deployed
- [ ] No broken imports

## Rollback Plan

If something goes wrong:

```bash
# Undo with git
git reset --hard HEAD
git clean -fd

# Or restore from backup
git checkout backup-branch
```

## Visual Comparison

### Before (47 items)
```
.
├── .env.example
├── .flake8
├── .gitignore
├── CHANGELOG.md
├── CREDENTIALS_SETUP_GUIDE.md
├── DEPLOYMENT_SECURITY_SUMMARY.md
├── DEPLOYMENT.md
├── DOCUMENTATION_IMPROVEMENTS_SUMMARY.md
├── DOCUMENTATION_MAP.md
├── Makefile
├── pyproject.toml
├── pytest.ini
├── QUICK_START.md
├── README.md
├── requirements-dev.txt
├── STAGING_DEPLOYMENT_SUMMARY.md
├── analyzeData/
├── cleanData/
├── config/
├── docs/
├── fetchData/
├── img/
├── infrastructure/
├── lambda_layer/
├── queryData/
├── retainData/
├── retrieveIdList/
├── scripts/
├── storeData/
├── testDatabaseConnection/
└── tests/
... (and more)
```

### After - Option A (18 items)
```
.
├── .env.example
├── .flake8
├── .gitignore
├── Makefile
├── pyproject.toml
├── pytest.ini
├── README.md
├── requirements-dev.txt
├── config/
├── docs/                    ← All documentation here
│   ├── deployment/
│   ├── guides/
│   ├── architecture/
│   └── CHANGELOG.md
├── infrastructure/
├── lambda_layer/
├── scripts/
├── src/                     ← All Lambda functions here
│   ├── analyzeData/
│   ├── cleanData/
│   ├── fetchData/
│   ├── queryData/
│   ├── retainData/
│   ├── retrieveIdList/
│   ├── storeData/
│   └── testDatabaseConnection/
└── tests/
```

### After - Option B (17 files + dirs)
```
.
├── .env.example
├── .flake8
├── .gitignore
├── Makefile
├── pyproject.toml
├── pytest.ini
├── README.md
├── requirements-dev.txt
├── analyzeData/
├── cleanData/
├── config/
├── docs/                    ← All documentation here
│   ├── QUICK_START.md
│   ├── CREDENTIALS_SETUP_GUIDE.md
│   ├── DEPLOYMENT.md
│   ├── DEPLOYMENT_SECURITY_SUMMARY.md
│   ├── STAGING_DEPLOYMENT_SUMMARY.md
│   ├── DOCUMENTATION_MAP.md
│   ├── DOCUMENTATION_IMPROVEMENTS_SUMMARY.md
│   └── CHANGELOG.md
├── fetchData/
├── img/
├── infrastructure/
├── lambda_layer/
├── queryData/
├── retainData/
├── retrieveIdList/
├── scripts/
├── storeData/
├── testDatabaseConnection/
└── tests/
```

## Recommendation

**Start with Option B (Documentation Only)**

**Why:**
1. Quick (30 minutes)
2. Low risk
3. Immediate improvement (32% reduction)
4. Can do Option A later if needed

**Then consider Option A later:**
- After documentation reorganization is stable
- When you have more time
- For maximum cleanliness

## Time Estimates

| Task | Option A | Option B |
|------|----------|----------|
| Create structure | 2 min | 1 min |
| Move files | 5 min | 3 min |
| Update references | 30 min | 15 min |
| Test | 15 min | 10 min |
| **Total** | **52 min** | **29 min** |

## Decision Matrix

| Criteria | Option A | Option B |
|----------|----------|----------|
| Impact | 62% reduction | 32% reduction |
| Time | 1 hour | 30 minutes |
| Risk | Low | Very Low |
| Maintainability | Excellent | Good |
| Industry Standard | Yes | Partial |

## Next Steps

1. **Choose option:** A or B
2. **Create backup:** `git checkout -b backup-before-reorg`
3. **Run commands:** Copy-paste from above
4. **Update references:** Use find/replace
5. **Test:** Check all links and scripts
6. **Commit:** Clear commit message
7. **Push:** Monitor first deployment

---

**Need help?** See [REPOSITORY_REORGANIZATION_PLAN.md](REPOSITORY_REORGANIZATION_PLAN.md) for detailed plan.
