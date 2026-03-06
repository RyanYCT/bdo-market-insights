# Images Directory

This directory contains all images used in the project documentation.

## Directory Structure

```
img/
├── architecture/     # System architecture diagrams
├── examples/         # Example outputs and visualizations
└── tools/           # Tool screenshots and interfaces
```

## Image Inventory

### Architecture Diagrams (`architecture/`)

| File | Description | Used In |
|------|-------------|---------|
| `arch_light_bg.png` | System architecture diagram (light theme with background) | README.md |
| `arch_light.png` | System architecture diagram (light theme) | Documentation |
| `arch_dark.png` | System architecture diagram (dark theme) | Documentation |

**Purpose:** Visual representation of the serverless ETL pipeline architecture showing Lambda functions, Step Functions, API Gateway, RDS, and DynamoDB.

### Example Outputs (`examples/`)

| File | Description | Used In |
|------|-------------|---------|
| `return_scatter.png` | Scatter plot of item profitability | README.md - Use Case 1 |
| `deboreka_necklace_trends.png` | Price and volume trends for Deboreka Necklace | README.md - Use Case 2 |
| `ocean_haze_ring_trends.png` | Price and volume trends for Ocean Haze Ring | README.md - Use Case 2 |

**Purpose:** Real-world examples demonstrating the system's analytical capabilities and output visualizations.

### Tool Screenshots (`tools/`)

| File | Description | Used In |
|------|-------------|---------|
| `postman_workspace_light.png` | Postman API testing workspace | README.md - API section |

**Purpose:** Screenshots of development and testing tools used with the system.

## Image Guidelines

### Adding New Images

1. **Choose the correct directory:**
   - `architecture/` - System diagrams, flowcharts, infrastructure
   - `examples/` - Data visualizations, output examples, charts
   - `tools/` - Tool screenshots, UI captures

2. **Naming convention:**
   - Use lowercase with underscores: `my_image_name.png`
   - Include theme suffix if applicable: `_light`, `_dark`, `_light_bg`
   - Be descriptive: `user_authentication_flow.png` not `diagram1.png`

3. **File format:**
   - Use PNG for screenshots and diagrams
   - Use SVG for vector graphics when possible
   - Optimize images before committing (use tools like TinyPNG)

4. **Update this README:**
   - Add entry to the appropriate table
   - Include description and usage location

### Image Optimization

Before committing images:
- Compress PNG files (aim for < 500KB)
- Remove unnecessary metadata
- Use appropriate resolution (2x for retina displays)

### Updating Images

When updating an existing image:
1. Replace the file with the same name
2. Update the "Last Updated" date in this README
3. Verify all references in documentation still work

## Image References

### In Markdown

```markdown
# Relative path from root
![Architecture](./img/architecture/arch_light_bg.png)

# Relative path from subdirectory
![Architecture](../img/architecture/arch_light_bg.png)
```

### Checking References

To find all references to an image:
```bash
# Linux/macOS
grep -r "arch_light_bg.png" .

# Windows PowerShell
Select-String -Path . -Pattern "arch_light_bg.png" -Recurse
```

## Maintenance

### Regular Tasks

- [ ] Review image sizes quarterly (target: < 500KB each)
- [ ] Check for unused images
- [ ] Verify all image links work
- [ ] Update dark/light theme variants together
- [ ] Ensure consistent styling across diagrams

### Unused Images

Before deleting an image:
1. Search for references in all documentation
2. Check if it's used in archived documentation
3. Consider if it might be needed for future use
4. Document deletion in CHANGELOG.md

## Image Creation Tools

### Recommended Tools

**Architecture Diagrams:**
- [draw.io](https://app.diagrams.net/) - Free, web-based
- [Lucidchart](https://www.lucidchart.com/) - Professional diagrams
- [Mermaid](https://mermaid.js.org/) - Code-based diagrams

**Data Visualizations:**
- Python: matplotlib, seaborn, plotly
- JavaScript: D3.js, Chart.js
- Excel/Google Sheets for simple charts

**Screenshots:**
- Windows: Snipping Tool, Snip & Sketch
- macOS: Command + Shift + 4
- Cross-platform: ShareX, Greenshot

**Image Optimization:**
- [TinyPNG](https://tinypng.com/) - PNG compression
- [Squoosh](https://squoosh.app/) - Image optimization
- [ImageOptim](https://imageoptim.com/) - macOS optimization

## Theme Support

### Light/Dark Variants

When creating diagrams, provide both light and dark theme variants:
- `diagram_name_light.png` - Light theme (white/light background)
- `diagram_name_dark.png` - Dark theme (dark background)
- `diagram_name_light_bg.png` - Light theme with colored background

### GitHub Theme Detection

Use HTML to show different images based on GitHub theme:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./img/architecture/arch_dark.png">
  <source media="(prefers-color-scheme: light)" srcset="./img/architecture/arch_light.png">
  <img alt="Architecture" src="./img/architecture/arch_light_bg.png">
</picture>
```

## Statistics

- **Total Images:** 7
- **Architecture Diagrams:** 3
- **Example Outputs:** 3
- **Tool Screenshots:** 1
- **Total Size:** ~2.5 MB (estimated)

Last updated: 2024-03-06
